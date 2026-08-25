import hashlib
import json
import re
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from threading import BoundedSemaphore
from threading import RLock
from time import perf_counter
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.skill_errors import SkillConflictError
from app.core.skill_errors import SkillExecutionError
from app.core.skill_errors import SkillExecutionTimeoutError
from app.core.skill_errors import SkillHandlerNotAllowedError
from app.core.skill_errors import SkillIdempotencyConflictError
from app.core.skill_errors import SkillInputValidationError
from app.core.skill_errors import SkillInvocationInProgressError
from app.core.skill_errors import SkillNotFoundError
from app.core.skill_errors import SkillOutputLimitError
from app.core.skill_errors import SkillOutputValidationError
from app.core.skill_errors import SkillRuntimeBusyError
from app.core.skill_errors import SkillRuntimeError
from app.core.skill_errors import SkillSchemaError
from app.core.skill_errors import SkillStateError
from app.core.skill_errors import SkillValidationError
from app.models.skill import SkillDefinition
from app.models.skill import SkillInvocation
from app.models.skill import SkillVersion
from app.core.skill_observability import log_skill_runtime_event
from app.services.isolated_skill_executor import IsolatedSkillExecutor
from app.services.skill_runtime_context import WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
from app.services.skill_runtime_context import WorkLearningRuntimeContext
from app.services.skill_runtime_context import normalize_work_learning_runtime_context
from app.repositories.skill_repository import SkillRepository


SkillHandler = Callable[..., Any]

RUNTIME_KINDS = frozenset({
    "internal_python",
    "plugin",
})

ACTOR_TYPES = frozenset({
    "user",
    "agent",
    "system",
    "integration",
})

IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._:-]{0,254}$"
)

MAX_INPUT_BYTES = 64 * 1024
MAX_JSON_DEPTH = 10
MAX_RUNTIME_WORKERS = 32

INVOCATION_IDEMPOTENCY_CONSTRAINT = (
    "uq_skill_invocations_idempotency_scope"
)


@dataclass(frozen=True)
class SkillInvocationActor:
    actor_type: str
    actor_reference: str
    actor_user_id: int | None = None


@dataclass(frozen=True)
class SkillInvocationResult:
    invocation: SkillInvocation
    output: Any
    duplicate: bool


@dataclass(frozen=True)
class RegisteredSkillHandler:
    runtime_kind: str
    handler_reference: str
    handler: SkillHandler
    trusted_for_autonomy: bool = False
    autonomy_entrypoint: str | None = None
    runtime_context_protocol: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_text(
    value: str,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise SkillValidationError(
            f"{field_name} deve ser texto."
        )

    normalized = value.strip()

    if not normalized:
        raise SkillValidationError(
            f"{field_name} não pode ser vazio."
        )

    if len(normalized) > max_length:
        raise SkillValidationError(
            f"{field_name} excede {max_length} caracteres."
        )

    return normalized


def _positive_id(
    value: int,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise SkillValidationError(
            f"{field_name} inválido."
        )

    return value


def _optional_positive_id(
    value: int | None,
    *,
    field_name: str,
) -> int | None:
    if value is None:
        return None

    return _positive_id(
        value,
        field_name=field_name,
    )


def _json_depth(
    value: Any,
    *,
    current_depth: int = 1,
    ancestors: frozenset[int] = frozenset(),
) -> int:
    if current_depth > MAX_JSON_DEPTH:
        return current_depth

    if isinstance(value, dict):
        if not all(
            isinstance(key, str)
            for key in value
        ):
            raise TypeError(
                "JSON object keys must be strings"
            )

        identity = id(value)

        if identity in ancestors:
            raise ValueError(
                "cyclic JSON value"
            )

        if not value:
            return current_depth

        child_ancestors = ancestors | {
            identity
        }

        return max(
            _json_depth(
                child,
                current_depth=current_depth + 1,
                ancestors=child_ancestors,
            )
            for child in value.values()
        )

    if isinstance(value, list):
        identity = id(value)

        if identity in ancestors:
            raise ValueError(
                "cyclic JSON value"
            )

        if not value:
            return current_depth

        child_ancestors = ancestors | {
            identity
        }

        return max(
            _json_depth(
                child,
                current_depth=current_depth + 1,
                ancestors=child_ancestors,
            )
            for child in value
        )

    return current_depth


def _canonical_json(
    value: Any,
    *,
    field_name: str,
    max_bytes: int,
) -> tuple[Any, bytes]:
    try:
        depth = _json_depth(value)

        if depth > MAX_JSON_DEPTH:
            raise SkillValidationError(
                f"{field_name} excede profundidade JSON "
                f"{MAX_JSON_DEPTH}."
            )

        serialized = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except SkillValidationError:
        raise
    except (TypeError, ValueError) as error:
        raise SkillValidationError(
            f"{field_name} não é JSON válido."
        ) from error

    encoded = serialized.encode(
        "utf-8"
    )

    if len(encoded) > max_bytes:
        raise SkillValidationError(
            f"{field_name} excede {max_bytes} bytes."
        )

    return json.loads(serialized), encoded


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(
        value
    ).hexdigest()


def _fingerprint(
    *,
    version: SkillVersion,
    actor: SkillInvocationActor,
    normalized_input: Any,
    runtime_context_protocol: str | None = None,
    runtime_context_digest: str | None = None,
) -> str:
    if (runtime_context_protocol is None) != (runtime_context_digest is None):
        raise SkillValidationError(
            "runtime context fingerprint incompleto."
        )

    envelope = {
        "actor": {
            "actor_reference": actor.actor_reference,
            "actor_type": actor.actor_type,
            "actor_user_id": actor.actor_user_id,
        },
        "input": normalized_input,
        "manifest_digest": version.manifest_digest,
        "skill_version_id": version.id,
    }

    if runtime_context_protocol is not None:
        envelope["runtime_context"] = {
            "digest": runtime_context_digest,
            "protocol": runtime_context_protocol,
        }

    serialized = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    return _digest_bytes(serialized)


def _normalized_actor(
    actor: SkillInvocationActor,
) -> SkillInvocationActor:
    if not isinstance(
        actor,
        SkillInvocationActor,
    ):
        raise SkillValidationError(
            "actor inválido."
        )

    actor_type = _required_text(
        actor.actor_type,
        field_name="actor_type",
        max_length=24,
    ).lower()

    if actor_type not in ACTOR_TYPES:
        raise SkillValidationError(
            "actor_type inválido."
        )

    actor_reference = _required_text(
        actor.actor_reference,
        field_name="actor_reference",
        max_length=255,
    )

    actor_user_id = _optional_positive_id(
        actor.actor_user_id,
        field_name="actor_user_id",
    )

    if (
        actor_type == "user"
        and actor_user_id is None
    ):
        raise SkillValidationError(
            "actor_user_id é obrigatório "
            "para ator user."
        )

    if (
        actor_type != "user"
        and actor_user_id is not None
    ):
        raise SkillValidationError(
            "actor_user_id somente é permitido "
            "para ator user."
        )

    return SkillInvocationActor(
        actor_type=actor_type,
        actor_reference=actor_reference,
        actor_user_id=actor_user_id,
    )


def _normalized_idempotency_key(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise SkillValidationError(
            "idempotency_key deve ser texto."
        )

    normalized = value.strip().lower()

    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(
        normalized
    ):
        raise SkillValidationError(
            "idempotency_key inválida."
        )

    return normalized


def _reject_remote_refs(
    value: Any,
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                key == "$ref"
                and (
                    not isinstance(child, str)
                    or not child.startswith("#")
                )
            ):
                raise SkillSchemaError(
                    "Referência JSON Schema externa "
                    "não é permitida."
                )

            _reject_remote_refs(
                child
            )

    elif isinstance(value, list):
        for child in value:
            _reject_remote_refs(
                child
            )


def _schema_validator(
    schema: dict[str, Any],
) -> Draft202012Validator:
    try:
        _reject_remote_refs(schema)
        Draft202012Validator.check_schema(
            schema
        )
    except SkillSchemaError:
        raise
    except SchemaError as error:
        raise SkillSchemaError(
            "JSON Schema publicado é inválido."
        ) from error

    return Draft202012Validator(
        schema
    )


def _validate_payload(
    *,
    payload: Any,
    schema: dict[str, Any],
    field_name: str,
) -> None:
    validator = _schema_validator(
        schema
    )

    try:
        validator.validate(
            payload
        )
    except ValidationError as error:
        if field_name == "input":
            raise SkillInputValidationError(
                "Entrada não satisfaz o "
                "contrato publicado."
            ) from error

        raise SkillOutputValidationError(
            "Saída não satisfaz o "
            "contrato publicado."
        ) from error


def _constraint_name(
    error: IntegrityError,
) -> str | None:
    original = getattr(
        error,
        "orig",
        None,
    )
    diagnostic = getattr(
        original,
        "diag",
        None,
    )
    return getattr(
        diagnostic,
        "constraint_name",
        None,
    )


class SkillHandlerRegistry:
    """
    Allowlist explícita de handlers executáveis.

    O catálogo persistido descreve um handler, mas nunca
    importa código por conta própria. Somente referências
    previamente registradas neste objeto podem executar.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._handlers: dict[
            tuple[str, str],
            RegisteredSkillHandler,
        ] = {}

    def register(
        self,
        *,
        runtime_kind: str,
        handler_reference: str,
        handler: SkillHandler,
        trusted_for_autonomy: bool = False,
        autonomy_entrypoint: str | None = None,
        runtime_context_protocol: str | None = None,
    ) -> RegisteredSkillHandler:
        normalized_runtime = _required_text(
            runtime_kind,
            field_name="runtime_kind",
            max_length=24,
        ).lower()

        if normalized_runtime not in RUNTIME_KINDS:
            raise SkillValidationError(
                "runtime_kind inválido."
            )

        normalized_reference = _required_text(
            handler_reference,
            field_name="handler_reference",
            max_length=320,
        )

        if not callable(handler):
            raise SkillValidationError(
                "handler deve ser callable."
            )

        if not isinstance(trusted_for_autonomy, bool):
            raise SkillValidationError(
                "trusted_for_autonomy deve ser booleano."
            )

        normalized_autonomy_entrypoint = None
        if autonomy_entrypoint is not None:
            normalized_autonomy_entrypoint = _required_text(
                autonomy_entrypoint,
                field_name="autonomy_entrypoint",
                max_length=320,
            )

        if (
            normalized_autonomy_entrypoint is not None
            and not trusted_for_autonomy
        ):
            raise SkillValidationError(
                "autonomy_entrypoint exige trust autônomo explícito."
            )

        if (
            trusted_for_autonomy
            and normalized_runtime == "internal_python"
            and normalized_autonomy_entrypoint is None
        ):
            raise SkillValidationError(
                "Handler internal_python confiado para autonomia "
                "exige autonomy_entrypoint isolado."
            )

        if (
            normalized_runtime != "internal_python"
            and normalized_autonomy_entrypoint is not None
        ):
            raise SkillValidationError(
                "autonomy_entrypoint isolado é permitido apenas "
                "para internal_python."
            )

        normalized_runtime_context_protocol = None
        if runtime_context_protocol is not None:
            normalized_runtime_context_protocol = _required_text(
                runtime_context_protocol,
                field_name="runtime_context_protocol",
                max_length=64,
            ).lower()
            if (
                normalized_runtime_context_protocol
                != WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
            ):
                raise SkillValidationError(
                    "runtime_context_protocol inválido."
                )
            if (
                normalized_runtime != "internal_python"
                or not trusted_for_autonomy
                or normalized_autonomy_entrypoint is None
            ):
                raise SkillValidationError(
                    "runtime context exige handler internal_python "
                    "confiado e isolado."
                )

        registered = RegisteredSkillHandler(
            runtime_kind=normalized_runtime,
            handler_reference=normalized_reference,
            handler=handler,
            trusted_for_autonomy=trusted_for_autonomy,
            autonomy_entrypoint=normalized_autonomy_entrypoint,
            runtime_context_protocol=(
                normalized_runtime_context_protocol
            ),
        )
        identity = (
            normalized_runtime,
            normalized_reference,
        )

        with self._lock:
            existing = self._handlers.get(
                identity
            )

            if existing is not None:
                if (
                    existing.handler is handler
                    and existing.trusted_for_autonomy
                    == trusted_for_autonomy
                    and existing.autonomy_entrypoint
                    == normalized_autonomy_entrypoint
                    and existing.runtime_context_protocol
                    == normalized_runtime_context_protocol
                ):
                    return existing

                raise SkillConflictError(
                    "Handler já registrado para "
                    "esta referência."
                )

            self._handlers[
                identity
            ] = registered

        return registered

    def unregister(
        self,
        *,
        runtime_kind: str,
        handler_reference: str,
    ) -> bool:
        identity = (
            runtime_kind.strip().lower(),
            handler_reference.strip(),
        )

        with self._lock:
            return (
                self._handlers.pop(
                    identity,
                    None,
                )
                is not None
            )

    def resolve(
        self,
        *,
        runtime_kind: str,
        handler_reference: str,
    ) -> RegisteredSkillHandler:
        identity = (
            runtime_kind,
            handler_reference,
        )

        with self._lock:
            registered = self._handlers.get(
                identity
            )

        if registered is None:
            raise SkillHandlerNotAllowedError(
                "Handler não autorizado no "
                "runtime de Agent Skills."
            )

        return registered

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


class BoundedSkillExecutor:
    """
    Executor síncrono com fila efetivamente limitada.

    O semaphore limita tarefas submetidas. Um timeout encerra
    a espera do chamador e mantém o slot ocupado até o handler
    realmente terminar, evitando que timeouts criem fila sem limite.
    Python não oferece cancelamento seguro de thread já iniciada;
    essa limitação é tratada como fronteira explícita do 23C.
    """

    def __init__(
        self,
        *,
        max_workers: int = 4,
    ) -> None:
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers < 1
            or max_workers > MAX_RUNTIME_WORKERS
        ):
            raise SkillValidationError(
                "max_workers deve estar entre "
                f"1 e {MAX_RUNTIME_WORKERS}."
            )

        self.max_workers = max_workers
        self._semaphore = BoundedSemaphore(
            max_workers
        )
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=(
                "auneron-skill"
            ),
        )

    def execute(
        self,
        handler: SkillHandler,
        payload: Any,
        *,
        timeout_seconds: int,
    ) -> Any:
        acquired = self._semaphore.acquire(
            blocking=False
        )

        if not acquired:
            raise SkillRuntimeBusyError(
                "Runtime de Agent Skills "
                "sem capacidade disponível."
            )

        try:
            future = self._executor.submit(
                handler,
                payload,
            )
        except Exception as error:
            self._semaphore.release()
            raise SkillExecutionError(
                "Runtime não conseguiu iniciar "
                "a execução da skill."
            ) from error

        future.add_done_callback(
            self._release_slot
        )

        try:
            return future.result(
                timeout=timeout_seconds
            )
        except FutureTimeoutError as error:
            raise SkillExecutionTimeoutError(
                "Skill excedeu o timeout publicado."
            ) from error
        except Exception as error:
            raise SkillExecutionError(
                "Skill falhou durante a execução."
            ) from error

    def shutdown(
        self,
        *,
        wait: bool = True,
        cancel_futures: bool = False,
    ) -> None:
        self._executor.shutdown(
            wait=wait,
            cancel_futures=cancel_futures,
        )

    def _release_slot(
        self,
        _: Future[Any],
    ) -> None:
        self._semaphore.release()


skill_handler_registry = SkillHandlerRegistry()
skill_executor = BoundedSkillExecutor(
    max_workers=settings.skill_runtime_max_workers
)
skill_isolated_executor = IsolatedSkillExecutor(
    max_workers=settings.skill_autonomy_process_max_workers,
    kill_grace_seconds=(
        settings.skill_autonomy_process_kill_grace_seconds
    ),
)


class SkillRuntimeService:
    """
    Runtime interno do gate 23C.

    A classe recebe uma versão exata publicada, cria o ledger
    idempotente antes da execução, valida entrada/saída e persiste
    apenas saída bem-sucedida e metadados sanitizados. Ela não é
    endpoint público e não substitui RBAC/scope do gate 23D.
    """

    def __init__(
        self,
        db: Session,
        repository: SkillRepository | None = None,
        handler_registry: SkillHandlerRegistry | None = None,
        executor: BoundedSkillExecutor | None = None,
        isolated_executor: IsolatedSkillExecutor | None = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else SkillRepository(db)
        )
        self.handler_registry = (
            handler_registry
            if handler_registry is not None
            else skill_handler_registry
        )
        self.executor = (
            executor
            if executor is not None
            else skill_executor
        )
        self.isolated_executor = (
            isolated_executor
            if isolated_executor is not None
            else skill_isolated_executor
        )

    def invoke(
        self,
        version_id: int,
        *,
        actor: SkillInvocationActor,
        input_payload: Any,
        idempotency_key: str | None = None,
        isolated: bool = False,
        runtime_context: Any | None = None,
    ) -> SkillInvocationResult:
        if not isinstance(isolated, bool):
            raise SkillValidationError(
                "isolated deve ser booleano."
            )

        normalized_version_id = _positive_id(
            version_id,
            field_name="version_id",
        )
        normalized_actor = _normalized_actor(
            actor
        )
        normalized_idempotency_key = (
            _normalized_idempotency_key(
                idempotency_key
            )
        )
        normalized_input, input_bytes = (
            _canonical_json(
                input_payload,
                field_name="input_payload",
                max_bytes=MAX_INPUT_BYTES,
            )
        )

        version, skill = (
            self._require_executable_version(
                normalized_version_id
            )
        )

        normalized_runtime_context: WorkLearningRuntimeContext | None = None
        if runtime_context is not None:
            if not isolated:
                raise SkillValidationError(
                    "runtime context exige execução isolada."
                )
            if (
                version.runtime_kind != "internal_python"
                or version.execution_mode != "read_only"
            ):
                raise SkillHandlerNotAllowedError(
                    "runtime context é permitido apenas para "
                    "internal_python read_only."
                )

            manifest = (
                version.manifest
                if isinstance(version.manifest, dict)
                else {}
            )
            manifest_protocol = manifest.get(
                "runtime_context_protocol"
            )
            if (
                manifest_protocol
                != WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
            ):
                raise SkillHandlerNotAllowedError(
                    "Versão de skill não optou pelo runtime context."
                )

            normalized_runtime_context = (
                normalize_work_learning_runtime_context(
                    runtime_context,
                    expected_skill_version_id=version.id,
                )
            )

        if (
            version.execution_mode
            in {"mutating", "external"}
            and normalized_idempotency_key is None
        ):
            raise SkillValidationError(
                "Invocação mutating ou external "
                "exige idempotency_key."
            )

        request_fingerprint = _fingerprint(
            version=version,
            actor=normalized_actor,
            normalized_input=normalized_input,
            runtime_context_protocol=(
                normalized_runtime_context.protocol
                if normalized_runtime_context is not None
                else None
            ),
            runtime_context_digest=(
                normalized_runtime_context.digest
                if normalized_runtime_context is not None
                else None
            ),
        )
        input_digest = _digest_bytes(
            input_bytes
        )

        if normalized_idempotency_key is not None:
            existing = (
                self.repository
                .find_invocation_by_idempotency(
                    version_id=version.id,
                    actor_type=(
                        normalized_actor.actor_type
                    ),
                    actor_reference=(
                        normalized_actor.actor_reference
                    ),
                    idempotency_key=(
                        normalized_idempotency_key
                    ),
                )
            )

            if existing is not None:
                return self._replay(
                    existing,
                    request_fingerprint=(
                        request_fingerprint
                    ),
                )

        invocation = SkillInvocation(
            skill_version_id=version.id,
            actor_type=(
                normalized_actor.actor_type
            ),
            actor_reference=(
                normalized_actor.actor_reference
            ),
            actor_user_id=(
                normalized_actor.actor_user_id
            ),
            idempotency_key=(
                normalized_idempotency_key
            ),
            request_fingerprint=(
                request_fingerprint
            ),
            input_digest=input_digest,
            status="running",
            output_payload=None,
            output_digest=None,
            output_bytes=None,
            error_code=None,
            duration_ms=None,
            started_at=_utc_now(),
            finished_at=None,
        )

        try:
            self.repository.add_invocation(
                invocation
            )
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()

            if (
                _constraint_name(error)
                == INVOCATION_IDEMPOTENCY_CONSTRAINT
                and normalized_idempotency_key
                is not None
            ):
                concurrent = (
                    self.repository
                    .find_invocation_by_idempotency(
                        version_id=version.id,
                        actor_type=(
                            normalized_actor.actor_type
                        ),
                        actor_reference=(
                            normalized_actor.actor_reference
                        ),
                        idempotency_key=(
                            normalized_idempotency_key
                        ),
                    )
                )

                if concurrent is not None:
                    return self._replay(
                        concurrent,
                        request_fingerprint=(
                            request_fingerprint
                        ),
                    )

            raise SkillConflictError(
                "Conflito ao registrar "
                "invocação de skill."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        log_skill_runtime_event(
            "skill.runtime.started",
            invocation_id=invocation.id,
            skill_version_id=version.id,
            actor_type=normalized_actor.actor_type,
            status="running",
        )

        started = perf_counter()

        try:
            try:
                _validate_payload(
                    payload=normalized_input,
                    schema=version.input_schema,
                    field_name="input",
                )
            except SkillInputValidationError:
                self._finish_failure(
                    invocation.id,
                    status="rejected",
                    error_code=(
                        "input_validation_failed"
                    ),
                    started=started,
                )
                raise
            except SkillSchemaError:
                self._finish_failure(
                    invocation.id,
                    status="rejected",
                    error_code=(
                        "input_schema_invalid"
                    ),
                    started=started,
                )
                raise

            try:
                registered = (
                    self.handler_registry.resolve(
                        runtime_kind=(
                            version.runtime_kind
                        ),
                        handler_reference=(
                            version.handler_reference
                        ),
                    )
                )
            except SkillHandlerNotAllowedError:
                self._finish_failure(
                    invocation.id,
                    status="rejected",
                    error_code=(
                        "handler_not_allowed"
                    ),
                    started=started,
                )
                raise

            if (
                normalized_runtime_context is not None
                and registered.runtime_context_protocol
                != normalized_runtime_context.protocol
            ):
                self._finish_failure(
                    invocation.id,
                    status="rejected",
                    error_code=(
                        "runtime_context_handler_not_allowed"
                    ),
                    started=started,
                )
                raise SkillHandlerNotAllowedError(
                    "Handler não optou pelo runtime context exigido."
                )

            try:
                if isolated:
                    if (
                        not registered.trusted_for_autonomy
                        or registered.autonomy_entrypoint is None
                        or version.runtime_kind != "internal_python"
                    ):
                        self._finish_failure(
                            invocation.id,
                            status="rejected",
                            error_code=(
                                "isolated_handler_not_allowed"
                            ),
                            started=started,
                        )
                        raise SkillHandlerNotAllowedError(
                            "Handler não possui fronteira isolada "
                            "autônoma autorizada."
                        )

                    raw_output = self.isolated_executor.execute(
                        registered.autonomy_entrypoint,
                        normalized_input,
                        timeout_seconds=(
                            version.timeout_seconds
                        ),
                        max_output_bytes=(
                            version.max_output_bytes
                        ),
                        runtime_context_protocol=(
                            normalized_runtime_context.protocol
                            if normalized_runtime_context is not None
                            else None
                        ),
                        runtime_context=(
                            normalized_runtime_context.payload
                            if normalized_runtime_context is not None
                            else None
                        ),
                    )
                else:
                    raw_output = self.executor.execute(
                        registered.handler,
                        normalized_input,
                        timeout_seconds=(
                            version.timeout_seconds
                        ),
                    )
            except SkillRuntimeBusyError:
                self._finish_failure(
                    invocation.id,
                    status="rejected",
                    error_code=(
                        "isolated_runtime_busy"
                        if isolated
                        else "runtime_busy"
                    ),
                    started=started,
                )
                raise
            except SkillExecutionTimeoutError:
                self._finish_failure(
                    invocation.id,
                    status="timed_out",
                    error_code=(
                        "isolated_timeout_killed"
                        if isolated
                        else "timeout"
                    ),
                    started=started,
                )
                raise
            except SkillOutputLimitError:
                self._finish_failure(
                    invocation.id,
                    status="failed",
                    error_code=(
                        "isolated_output_limit"
                        if isolated
                        else "output_limit_or_json_invalid"
                    ),
                    started=started,
                )
                raise
            except SkillExecutionError:
                self._finish_failure(
                    invocation.id,
                    status="failed",
                    error_code=(
                        "isolated_execution_failed"
                        if isolated
                        else "execution_failed"
                    ),
                    started=started,
                )
                raise

            try:
                normalized_output, output_bytes = (
                    _canonical_json(
                        raw_output,
                        field_name="output_payload",
                        max_bytes=(
                            version.max_output_bytes
                        ),
                    )
                )
            except SkillValidationError as error:
                self._finish_failure(
                    invocation.id,
                    status="failed",
                    error_code=(
                        "output_limit_or_json_invalid"
                    ),
                    started=started,
                )
                raise SkillOutputLimitError(
                    "Saída não pode ser persistida "
                    "dentro do limite publicado."
                ) from error

            try:
                _validate_payload(
                    payload=normalized_output,
                    schema=version.output_schema,
                    field_name="output",
                )
            except SkillOutputValidationError:
                self._finish_failure(
                    invocation.id,
                    status="failed",
                    error_code=(
                        "output_validation_failed"
                    ),
                    started=started,
                )
                raise
            except SkillSchemaError:
                self._finish_failure(
                    invocation.id,
                    status="failed",
                    error_code=(
                        "output_schema_invalid"
                    ),
                    started=started,
                )
                raise

            invocation = self._finish_success(
                invocation.id,
                output=normalized_output,
                output_bytes=output_bytes,
                started=started,
            )

            return SkillInvocationResult(
                invocation=invocation,
                output=normalized_output,
                duplicate=False,
            )
        except Exception:
            raise

    def get_invocation(
        self,
        invocation_id: int,
    ) -> SkillInvocation:
        normalized_id = _positive_id(
            invocation_id,
            field_name="invocation_id",
        )
        invocation = (
            self.repository.get_invocation(
                normalized_id
            )
        )

        if invocation is None:
            raise SkillNotFoundError(
                "Invocação de skill "
                "não encontrada."
            )

        return invocation

    def list_invocations(
        self,
        version_id: int,
        *,
        limit: int = 100,
    ) -> tuple[SkillInvocation, ...]:
        normalized_version_id = _positive_id(
            version_id,
            field_name="version_id",
        )

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 100
        ):
            raise SkillValidationError(
                "limit deve estar entre 1 e 100."
            )

        return tuple(
            self.repository
            .list_invocations_for_version(
                normalized_version_id,
                limit=limit,
            )
        )

    def _require_executable_version(
        self,
        version_id: int,
    ) -> tuple[
        SkillVersion,
        SkillDefinition,
    ]:
        version = self.repository.get_version(
            version_id
        )

        if version is None:
            raise SkillNotFoundError(
                "Versão de skill não encontrada."
            )

        if version.status != "published":
            raise SkillStateError(
                "Execução exige versão publicada."
            )

        skill = self.repository.get_skill(
            version.skill_id
        )

        if skill is None:
            raise SkillNotFoundError(
                "Skill não encontrada."
            )

        if skill.status != "active":
            raise SkillStateError(
                "Execução exige skill ativa."
            )

        return version, skill

    def _replay(
        self,
        invocation: SkillInvocation,
        *,
        request_fingerprint: str,
    ) -> SkillInvocationResult:
        if (
            invocation.request_fingerprint
            != request_fingerprint
        ):
            raise SkillIdempotencyConflictError(
                "idempotency_key já foi usada "
                "com outro pedido."
            )

        log_skill_runtime_event(
            "skill.runtime.replay",
            invocation_id=invocation.id,
            skill_version_id=invocation.skill_version_id,
            actor_type=invocation.actor_type,
            status=invocation.status,
            duplicate=True,
        )

        if invocation.status == "running":
            raise SkillInvocationInProgressError(
                "Invocação idempotente "
                "ainda está em execução."
            )

        if invocation.status == "succeeded":
            envelope = invocation.output_payload

            if (
                not isinstance(envelope, dict)
                or "value" not in envelope
            ):
                raise SkillRuntimeError(
                    "Histórico idempotente "
                    "de saída está inconsistente."
                )

            return SkillInvocationResult(
                invocation=invocation,
                output=envelope["value"],
                duplicate=True,
            )

        raise self._replayed_error(
            invocation.error_code
        )

    @staticmethod
    def _replayed_error(
        error_code: str | None,
    ) -> Exception:
        mapping: dict[
            str,
            type[Exception],
        ] = {
            "handler_not_allowed": (
                SkillHandlerNotAllowedError
            ),
            "isolated_handler_not_allowed": (
                SkillHandlerNotAllowedError
            ),
            "input_schema_invalid": (
                SkillSchemaError
            ),
            "runtime_busy": (
                SkillRuntimeBusyError
            ),
            "isolated_runtime_busy": (
                SkillRuntimeBusyError
            ),
            "timeout": (
                SkillExecutionTimeoutError
            ),
            "isolated_timeout_killed": (
                SkillExecutionTimeoutError
            ),
            "execution_failed": (
                SkillExecutionError
            ),
            "isolated_execution_failed": (
                SkillExecutionError
            ),
            "isolated_output_limit": (
                SkillOutputLimitError
            ),
            "output_limit_or_json_invalid": (
                SkillOutputLimitError
            ),
            "output_validation_failed": (
                SkillOutputValidationError
            ),
            "output_schema_invalid": (
                SkillSchemaError
            ),
        }

        if error_code == "input_validation_failed":
            return SkillInputValidationError(
                "Entrada não satisfaz o "
                "contrato publicado."
            )

        error_type = mapping.get(
            error_code or "",
            SkillRuntimeError,
        )

        return error_type(
            "Invocação idempotente "
            "já terminou com falha."
        )

    def _finish_success(
        self,
        invocation_id: int,
        *,
        output: Any,
        output_bytes: bytes,
        started: float,
    ) -> SkillInvocation:
        invocation = self._locked_invocation(
            invocation_id
        )
        finished_at = _utc_now()
        duration_ms = max(
            0,
            int(
                (perf_counter() - started)
                * 1000
            ),
        )

        invocation.status = "succeeded"
        invocation.output_payload = {
            "value": output
        }
        invocation.output_digest = (
            _digest_bytes(
                output_bytes
            )
        )
        invocation.output_bytes = len(
            output_bytes
        )
        invocation.error_code = None
        invocation.duration_ms = duration_ms
        invocation.finished_at = finished_at

        try:
            self.db.flush()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        log_skill_runtime_event(
            "skill.runtime.finished",
            invocation_id=invocation.id,
            skill_version_id=invocation.skill_version_id,
            actor_type=invocation.actor_type,
            status=invocation.status,
            duration_ms=invocation.duration_ms,
            output_bytes=invocation.output_bytes,
        )

        return invocation

    def _finish_failure(
        self,
        invocation_id: int,
        *,
        status: str,
        error_code: str,
        started: float,
    ) -> SkillInvocation:
        if status not in {
            "failed",
            "timed_out",
            "rejected",
        }:
            raise SkillValidationError(
                "status terminal inválido."
            )

        invocation = self._locked_invocation(
            invocation_id
        )
        duration_ms = max(
            0,
            int(
                (perf_counter() - started)
                * 1000
            ),
        )

        invocation.status = status
        invocation.output_payload = None
        invocation.output_digest = None
        invocation.output_bytes = None
        invocation.error_code = error_code
        invocation.duration_ms = duration_ms
        invocation.finished_at = _utc_now()

        try:
            self.db.flush()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        log_skill_runtime_event(
            "skill.runtime.finished",
            invocation_id=invocation.id,
            skill_version_id=invocation.skill_version_id,
            actor_type=invocation.actor_type,
            status=invocation.status,
            duration_ms=invocation.duration_ms,
            error_code=invocation.error_code,
        )

        return invocation

    def recover_stale_invocations(
        self,
        *,
        now: datetime | None = None,
        stale_after_seconds: int | None = None,
        limit: int | None = None,
    ) -> tuple[SkillInvocation, ...]:
        effective_now = now or _utc_now()
        if (
            effective_now.tzinfo is None
            or effective_now.utcoffset() is None
        ):
            raise SkillValidationError(
                "now deve possuir timezone."
            )
        effective_now = effective_now.astimezone(
            timezone.utc
        )

        effective_stale_seconds = (
            settings.skill_stale_running_seconds
            if stale_after_seconds is None
            else stale_after_seconds
        )
        if (
            isinstance(effective_stale_seconds, bool)
            or not isinstance(effective_stale_seconds, int)
            or effective_stale_seconds < 301
            or effective_stale_seconds > 86400
        ):
            raise SkillValidationError(
                "stale_after_seconds deve estar "
                "entre 301 e 86400."
            )

        effective_limit = (
            settings.skill_recovery_batch_size
            if limit is None
            else limit
        )
        if (
            isinstance(effective_limit, bool)
            or not isinstance(effective_limit, int)
            or effective_limit < 1
            or effective_limit > 1000
        ):
            raise SkillValidationError(
                "limit deve estar entre 1 e 1000."
            )

        cutoff = effective_now - timedelta(
            seconds=effective_stale_seconds
        )

        try:
            stale = (
                self.repository
                .lock_stale_running_invocations(
                    cutoff=cutoff,
                    limit=effective_limit,
                )
            )

            for invocation in stale:
                duration_ms = max(
                    0,
                    int(
                        (
                            effective_now
                            - invocation.started_at
                        ).total_seconds()
                        * 1000
                    ),
                )
                invocation.status = "failed"
                invocation.output_payload = None
                invocation.output_digest = None
                invocation.output_bytes = None
                invocation.error_code = (
                    "stale_running_recovered"
                )
                invocation.duration_ms = duration_ms
                invocation.finished_at = effective_now

            self.db.flush()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        for invocation in stale:
            log_skill_runtime_event(
                "skill.runtime.stale_recovered",
                invocation_id=invocation.id,
                skill_version_id=(
                    invocation.skill_version_id
                ),
                actor_type=invocation.actor_type,
                status=invocation.status,
                duration_ms=invocation.duration_ms,
                error_code=invocation.error_code,
            )

        return tuple(stale)

    def _locked_invocation(
        self,
        invocation_id: int,
    ) -> SkillInvocation:
        invocation = (
            self.repository.lock_invocation(
                invocation_id
            )
        )

        if invocation is None:
            raise SkillNotFoundError(
                "Invocação de skill "
                "não encontrada."
            )

        if invocation.status != "running":
            raise SkillStateError(
                "Invocação já está em "
                "estado terminal."
            )

        return invocation
