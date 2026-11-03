import re
from collections.abc import Iterator
from datetime import date
from typing import Any
from typing import Final

from jsonschema import Draft202012Validator

from app.intelligence.gateway.contracts import REAL_DATA_ALLOWED
from app.intelligence.gateway.contracts import DataClassification
from app.intelligence.planning.admission import AdmissionDecision
from app.intelligence.planning.admission import PlanAdmission
from app.intelligence.planning.admission import StepApproval
from app.intelligence.planning.authority import AccountAccess
from app.intelligence.planning.authority import AuthorityContextError
from app.intelligence.planning.authority import SyntheticAuthorityContext
from app.intelligence.planning.canonical import CanonicalError
from app.intelligence.planning.canonical import InputError
from app.intelligence.planning.canonical import derive_idempotency_key
from app.intelligence.planning.canonical import scan_value
from app.intelligence.planning.canonical import seal
from app.intelligence.planning.canonical import strict_parse
from app.intelligence.planning.capabilities import PINNED_REGISTRY_DIGEST
from app.intelligence.planning.capabilities import ApprovalKind
from app.intelligence.planning.capabilities import CapabilityDefinition
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.capabilities import EffectClass
from app.intelligence.planning.capabilities import RegistrySnapshotError
from app.intelligence.planning.capabilities import snapshot_registry
from app.intelligence.planning.codes import RejectionCode
from app.intelligence.planning.codes import Violation
from app.intelligence.planning.codes import make_violation
from app.intelligence.planning.dag import analyze_dag
from app.intelligence.planning.policy import ActionPolicyEntry
from app.intelligence.planning.policy import AutonomyEntry
from app.intelligence.planning.policy import PolicyReference
from app.intelligence.planning.policy import reconcile_capability
from app.intelligence.planning.schema import CONTRACT
from app.intelligence.planning.schema import predicate_problem
from app.intelligence.planning.schema import skeleton_schema
from app.intelligence.planning.canonical import SERVER_ONLY_FIELDS


SUPPORTED_PROPOSER_KINDS: Final[frozenset[str]] = frozenset({"llm"})

# A step reference smuggled into free text would be an attempt at dataflow
# between steps, which plan.v1 does not have.
_DATAFLOW = re.compile(
    r"\{\{|\}\}|\$\{|\$[A-Za-z_]+\.|<<|\bsteps?\s*[\[.]|\bstep_outputs?\b"
)
# Structure heuristics only (long opaque runs / well-known key shapes);
# the closed schemas already leave almost no free text to hide a value in.
_TOKEN_LIKE = re.compile(
    r"[A-Za-z0-9+/_=-]{32,}"
    r"|\b[0-9a-fA-F]{24,}\b"
    r"|\b(?:sk|pk|ghp|gho|xox[abp])[_-][A-Za-z0-9]{16,}"
)

_SKELETON_VALIDATOR: Final[Draft202012Validator] = Draft202012Validator(
    skeleton_schema()
)


def _json_path(parts: Any) -> str:
    path = "$"

    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"

    return path


def _free_text(plan: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """(path, text) for every field that can carry arbitrary text."""
    yield "$.summary", plan["summary"]

    for index, step in enumerate(plan["steps"]):
        yield f"$.steps[{index}].rationale", step["rationale"]

        for name, value in sorted(step["params"].items()):
            if isinstance(value, str):
                yield f"$.steps[{index}].params.{name}", value


def _reject(
    registry_digest: str | None,
    violations: list[Violation],
    *,
    context_digest: str | None = None,
    initiator_ref: str | None = None,
) -> PlanAdmission:
    # Takes the digest of the SNAPSHOT (or None), never reads a digest
    # from the object the caller passed in.
    return PlanAdmission(
        decision=AdmissionDecision.REJECTED,
        violations=tuple(sorted(set(violations))),
        registry_digest=registry_digest,
        context_digest=context_digest,
        execution_digest=None,
        presentation_digest=None,
        idempotency_key=None,
        objective_ref=None,
        plan_version=None,
        step_keys=(),
        mutating_step_keys=(),
        requires_step_approvals=(),
        separation_requirements=(),
        initiator_ref=initiator_ref,
    )


def _defect(error: Exception) -> PlanAdmission:
    """Typed result for a failure of the validator itself. The detail is
    a built-in exception class name or a fixed token: never a message,
    never input content."""
    kind = type(error)
    detail = kind.__name__ if kind.__module__ == "builtins" else "non_builtin"

    return _reject(
        None,
        [make_violation(RejectionCode.VALIDATOR_INTERNAL_ERROR, "$", detail)],
    )


# ----------------------------------------------------------------------
# snapshots of the server-side objects. Each is read ONCE, field by field
# from its instance dictionary (no property, no __getattr__, no foreign
# method), with exact types; then rebuilt through its own constructor.
# Every later use reads only the copy. Expected malformed input raises
# _Malformed; anything else is a defect and propagates.
# ----------------------------------------------------------------------


class _Malformed(Exception):
    """Internal control flow only; never leaves this module."""


def _fields(value: Any, kind: type) -> dict[str, Any]:
    if not isinstance(value, kind):
        raise _Malformed()

    state = vars(value)

    if type(state) is not dict:
        raise _Malformed()

    return state


def _exact(value: Any, *kinds: type) -> Any:
    if type(value) not in kinds:
        raise _Malformed()

    return value


def _text_set(value: Any) -> frozenset[str]:
    if type(value) not in (frozenset, set) or not all(
        type(item) is str for item in value
    ):
        raise _Malformed()

    return frozenset(value)


def _plain_dict(value: Any) -> dict[Any, Any]:
    # Exact dict only: a mapping subclass could run code on every read.
    return dict(_exact(value, dict))


def _snapshot_authority(
    authority: Any,
) -> tuple[SyntheticAuthorityContext, str]:
    state = _fields(authority, SyntheticAuthorityContext)
    accounts: dict[str, AccountAccess] = {}

    for ref, access in _plain_dict(state.get("accounts")).items():
        access_state = _fields(access, AccountAccess)
        accounts[_exact(ref, str)] = AccountAccess(
            readable=_exact(access_state.get("readable"), bool),
            manageable=_exact(access_state.get("manageable"), bool),
        )

    if state.get("synthetic") is not True:
        raise _Malformed()

    try:
        # The constructor runs its checks again (including the refusal
        # in production); the digest applies the canonical value rules
        # to every text of the context (D1).
        copied = SyntheticAuthorityContext(
            initiator_ref=_exact(state.get("initiator_ref"), str),
            role=_exact(state.get("role"), str),
            permissions=_text_set(state.get("permissions")),
            accounts=accounts,
            session_elevated=_exact(state.get("session_elevated"), bool),
            synthetic=True,
        )
        return copied, copied.context_digest()
    except (AuthorityContextError, CanonicalError):
        raise _Malformed() from None


def _snapshot_policy(policy: PolicyReference) -> PolicyReference:
    state = _fields(policy, PolicyReference)
    actions: dict[str, ActionPolicyEntry] = {}
    autonomy: dict[str, AutonomyEntry] = {}
    routes: dict[str, frozenset[str]] = {}

    for key, entry in _plain_dict(state.get("actions")).items():
        entry_state = _fields(entry, ActionPolicyEntry)
        actions[_exact(key, str)] = ActionPolicyEntry(
            capability_kind=_exact(entry_state.get("capability_kind"), str),
            initiation_actor=_exact(entry_state.get("initiation_actor"), str),
            authority_model=_exact(entry_state.get("authority_model"), str),
            required_authority=_exact(
                entry_state.get("required_authority"), str
            ),
            execution_corridor=_exact(
                entry_state.get("execution_corridor"), str
            ),
        )

    for key, entry in _plain_dict(state.get("autonomy_by_effect")).items():
        entry_state = _fields(entry, AutonomyEntry)
        autonomy[_exact(key, str)] = AutonomyEntry(
            disposition=_exact(entry_state.get("disposition"), str),
            risk_level=_exact(entry_state.get("risk_level"), str),
            approver_permission=_exact(
                entry_state.get("approver_permission"), str, type(None)
            ),
        )

    for key, permissions in _plain_dict(
        state.get("read_route_permissions")
    ).items():
        routes[_exact(key, str)] = _text_set(permissions)

    return PolicyReference(
        actions=actions,
        autonomy_by_effect=autonomy,
        known_permissions=_text_set(state.get("known_permissions")),
        read_route_permissions=routes,
        source=_exact(state.get("source"), str),
    )


def validate_plan(
    raw: bytes | str,
    *,
    registry: CapabilityRegistry,
    policy: PolicyReference | None,
    authority: SyntheticAuthorityContext | None,
    proposer_kind: str = "llm",
    data_classification: DataClassification = DataClassification.SYNTHETIC,
    pinned_registry_digest: str = PINNED_REGISTRY_DIGEST,
) -> PlanAdmission:
    """
    Deterministic, fail-closed structural admissibility of a plan.v1
    proposal. Pure: no I/O, no database, no logging, no clock.

    The first stage that finds violations ends the run (all violations of
    that stage are returned, sorted by stage, code and path), so the same
    input always yields the same answer. A positive answer is NOT
    permission to execute anything: see PlanAdmission.

    Malformed server-side objects (registry, authority, policy) are typed
    rejections of their own stage. An unexpected failure of the validator
    itself is NOT an ordinary rejection: it is the typed defect result
    `validator_internal_error` (PlanAdmission.internal_defect is True),
    which every gate must treat as a failure to fix. An unsupported
    `proposer_kind` is a caller programming error and still raises.
    """
    if proposer_kind not in SUPPORTED_PROPOSER_KINDS:
        raise ValueError("Unsupported proposer_kind in this phase.")

    try:
        return _validate(
            raw,
            registry=registry,
            policy=policy,
            authority=authority,
            data_classification=data_classification,
            pinned_registry_digest=pinned_registry_digest,
        )
    except Exception as error:
        return _defect(error)


def _validate(
    raw: Any,
    *,
    registry: Any,
    policy: Any,
    authority: Any,
    data_classification: Any,
    pinned_registry_digest: Any,
) -> PlanAdmission:
    # -- 0. registry integrity ------------------------------------------
    # The digest is RECOMPUTED from a snapshot of what the registry holds
    # now (D2); the cached digest of the object passed in is never read.
    try:
        snapshot = snapshot_registry(registry)
    except RegistrySnapshotError:
        return _reject(
            None,
            [make_violation(RejectionCode.REGISTRY_INTEGRITY_FAILURE)],
        )

    registry_digest = snapshot.digest

    if registry_digest != pinned_registry_digest:
        return _reject(
            registry_digest,
            [make_violation(RejectionCode.REGISTRY_INTEGRITY_FAILURE)],
        )

    # Only the snapshot is used from here on.
    registry = snapshot
    limits = registry.limits

    # -- 1. input ----------------------------------------------------------
    try:
        plan = strict_parse(raw, limits)
    except InputError as error:
        return _reject(registry_digest, [make_violation(error.code)])

    # -- 2. contract -------------------------------------------------------
    found: list[Violation] = []
    contract = plan.get("contract")

    if "contract" in plan and contract != CONTRACT:
        if isinstance(contract, str) and contract.startswith("plan."):
            found.append(
                make_violation(
                    RejectionCode.CONTRACT_VERSION_UNSUPPORTED, "$.contract"
                )
            )
        else:
            found.append(
                make_violation(RejectionCode.CONTRACT_UNKNOWN, "$.contract")
            )

    if "registry_version" in plan and (
        plan["registry_version"] != registry.version
    ):
        found.append(
            make_violation(
                RejectionCode.REGISTRY_VERSION_MISMATCH, "$.registry_version"
            )
        )

    for name in SERVER_ONLY_FIELDS:
        if name in plan:
            found.append(
                make_violation(RejectionCode.SERVER_FIELD_FORBIDDEN, f"$.{name}")
            )

    if found:
        return _reject(registry_digest, found)

    # -- 3. value rules and closed structure ---------------------------
    found, json_depth = scan_value(plan, limits)

    if found:
        return _reject(registry_digest, found)

    errors = sorted(
        _SKELETON_VALIDATOR.iter_errors(plan),
        key=lambda e: (_json_path(e.absolute_path), e.validator),
    )

    if errors:
        return _reject(
            registry_digest,
            [
                make_violation(
                    RejectionCode.SCHEMA_VIOLATION,
                    _json_path(error.absolute_path),
                    str(error.validator),
                )
                for error in errors
            ],
        )

    steps: list[dict[str, Any]] = plan["steps"]

    # -- 4. limits ---------------------------------------------------------
    found = []

    if len(steps) > limits.max_steps:
        found.append(make_violation(RejectionCode.LIMIT_STEPS, "$.steps"))

    if json_depth > limits.max_json_depth:
        found.append(make_violation(RejectionCode.LIMIT_JSON_DEPTH))

    if len(plan["summary"]) > limits.max_summary_chars:
        found.append(
            make_violation(RejectionCode.LIMIT_SUMMARY_LENGTH, "$.summary")
        )

    for index, step in enumerate(steps):
        base = f"$.steps[{index}]"

        if len(step["depends_on"]) > limits.max_dependencies:
            found.append(
                make_violation(
                    RejectionCode.LIMIT_DEPENDENCIES, f"{base}.depends_on"
                )
            )

        if len(step["rationale"]) > limits.max_rationale_chars:
            found.append(
                make_violation(
                    RejectionCode.LIMIT_RATIONALE_LENGTH, f"{base}.rationale"
                )
            )

        if len(step["preconditions"]) > limits.max_preconditions:
            found.append(
                make_violation(
                    RejectionCode.LIMIT_PREDICATES, f"{base}.preconditions"
                )
            )

        if len(step["expected_outcomes"]) > limits.max_expected_outcomes:
            found.append(
                make_violation(
                    RejectionCode.LIMIT_PREDICATES, f"{base}.expected_outcomes"
                )
            )

    if found:
        return _reject(registry_digest, found)

    # -- 5. capabilities ---------------------------------------------------
    found = []
    definitions: dict[int, CapabilityDefinition] = {}

    for index, step in enumerate(steps):
        base = f"$.steps[{index}]"
        capability_id = step["capability_id"]
        definition = registry.get(capability_id, step["capability_version"])

        if definition is None or not definition.registered:
            code = (
                RejectionCode.CAPABILITY_VERSION_UNSUPPORTED
                if registry.versions_of(capability_id)
                else RejectionCode.CAPABILITY_UNKNOWN
            )
            found.append(
                make_violation(
                    code,
                    f"{base}.capability_version"
                    if code is RejectionCode.CAPABILITY_VERSION_UNSUPPORTED
                    else f"{base}.capability_id",
                )
            )
            continue

        if not definition.enabled:
            found.append(
                make_violation(
                    RejectionCode.CAPABILITY_DISABLED, f"{base}.capability_id"
                )
            )
        elif not definition.llm_proposable:
            found.append(
                make_violation(
                    RejectionCode.CAPABILITY_NOT_LLM_PROPOSABLE,
                    f"{base}.capability_id",
                )
            )

        definitions[index] = definition

    if found:
        return _reject(registry_digest, found)

    # -- 6. parameters and predicates ---------------------------------------
    found = []

    for index, step in enumerate(steps):
        base = f"$.steps[{index}]"
        definition = definitions[index]

        for error in sorted(
            definition.validator.iter_errors(step["params"]),
            key=lambda e: (_json_path(e.absolute_path), e.validator),
        ):
            found.append(
                make_violation(
                    RejectionCode.PARAMS_INVALID,
                    base + ".params" + _json_path(error.absolute_path)[1:],
                    str(error.validator),
                )
            )

        due = step["params"].get("due_date")

        if isinstance(due, str):
            try:
                date.fromisoformat(due)
            except ValueError:
                found.append(
                    make_violation(
                        RejectionCode.PARAMS_SEMANTIC_INVALID,
                        f"{base}.params.due_date",
                        "calendar_date",
                    )
                )

        for name in ("preconditions", "expected_outcomes"):
            for position, predicate in enumerate(step[name]):
                problem = predicate_problem(predicate)

                if problem is not None:
                    found.append(
                        make_violation(
                            RejectionCode.PREDICATE_INVALID,
                            f"{base}.{name}[{position}]",
                            problem,
                        )
                    )

    if found:
        return _reject(registry_digest, found)

    # -- 7. DAG ------------------------------------------------------------
    dag = analyze_dag(steps, limits)

    if dag.violations:
        return _reject(registry_digest, list(dag.violations))

    # -- 8. effects ----------------------------------------------------------
    found = []
    mutating = [
        index
        for index in range(len(steps))
        if definitions[index].effect_class is EffectClass.MUTATE
    ]

    if len(mutating) > limits.max_mutating_steps:
        found.append(
            make_violation(RejectionCode.EFFECT_MUTATING_LIMIT, "$.steps")
        )

    for index, step in enumerate(steps):
        for name, value in sorted(step["params"].items()):
            if isinstance(value, str) and _DATAFLOW.search(value):
                found.append(
                    make_violation(
                        RejectionCode.EFFECT_DATAFLOW_FORBIDDEN,
                        f"$.steps[{index}].params.{name}",
                    )
                )

    by_key = {step["step_key"]: step for step in steps}

    for index, step in enumerate(steps):
        for prerequisite in definitions[index].prerequisites:
            base = f"$.steps[{index}]"
            ancestors = dag.ancestors[step["step_key"]]

            if not any(
                by_key[key]["capability_id"] in prerequisite.any_of
                and all(
                    name in by_key[key]["params"]
                    and name in step["params"]
                    and by_key[key]["params"][name] == step["params"][name]
                    for name in prerequisite.bind
                )
                for key in sorted(ancestors)
            ):
                found.append(
                    make_violation(
                        RejectionCode.EFFECT_ELIGIBILITY_DEPENDENCY_MISSING,
                        f"{base}.depends_on",
                    )
                )

            fact, op, value = prerequisite.required_precondition

            if not any(
                item["fact"] == fact
                and item["op"] == op
                and item["value"] == value
                and type(item["value"]) is type(value)
                for item in step["preconditions"]
            ):
                found.append(
                    make_violation(
                        RejectionCode.EFFECT_ELIGIBILITY_PRECONDITION_MISSING,
                        f"{base}.preconditions",
                    )
                )

    if found:
        return _reject(registry_digest, found)

    # -- 9. data policy --------------------------------------------------------
    found = []

    if REAL_DATA_ALLOWED is not False or (
        data_classification is not DataClassification.SYNTHETIC
    ):
        found.append(make_violation(RejectionCode.DATA_REAL_DATA_REFUSED))

    for path, text in _free_text(plan):
        if _TOKEN_LIKE.search(text):
            found.append(
                make_violation(RejectionCode.DATA_TOKEN_LIKE_VALUE, path)
            )

    if found:
        return _reject(registry_digest, found)

    # -- 10. authority (synthetic context) ----------------------------------------
    # A missing, malformed or non-canonical context (D1) is one typed code.
    try:
        authority, context_digest = _snapshot_authority(authority)
    except _Malformed:
        return _reject(
            registry_digest,
            [make_violation(RejectionCode.AUTHORITY_CONTEXT_INVALID)],
        )

    initiator_ref = authority.initiator_ref
    found = []

    for index, step in enumerate(steps):
        base = f"$.steps[{index}]"
        definition = definitions[index]

        if not set(definition.required_permissions) <= authority.permissions:
            found.append(
                make_violation(
                    RejectionCode.AUTHORITY_PERMISSION_MISSING,
                    base,
                    "permission",
                )
            )

        account_ref = step["params"].get("account_ref")

        if isinstance(account_ref, str):
            access = authority.access_to(account_ref)
            needed = (
                access is not None and access.manageable
                if definition.effect_class is EffectClass.MUTATE
                else access is not None and access.readable
            )

            if not needed:
                # Same code for "does not exist" and "not yours".
                found.append(
                    make_violation(
                        RejectionCode.AUTHORITY_SCOPE_INACCESSIBLE,
                        f"{base}.params.account_ref",
                    )
                )

    if found:
        return _reject(
            registry_digest,
            found,
            context_digest=context_digest,
            initiator_ref=initiator_ref,
        )

    # -- 11. policy reconciliation -----------------------------------------------------
    if not isinstance(policy, PolicyReference):
        return _reject(
            registry_digest,
            [make_violation(RejectionCode.POLICY_REFERENCE_MISSING)],
            context_digest=context_digest,
            initiator_ref=initiator_ref,
        )

    try:
        policy = _snapshot_policy(policy)
    except _Malformed:
        return _reject(
            registry_digest,
            [make_violation(RejectionCode.POLICY_REFERENCE_INVALID)],
            context_digest=context_digest,
            initiator_ref=initiator_ref,
        )

    found = []
    used = {
        (definition.capability_id, definition.version): definition
        for definition in definitions.values()
    }

    for key in sorted(used):
        for token in reconcile_capability(used[key], policy):
            found.append(
                make_violation(
                    RejectionCode.POLICY_REFERENCE_MISMATCH,
                    f"$.capabilities.{key[0]}@{key[1]}",
                    token,
                )
            )

    if found:
        return _reject(
            registry_digest,
            found,
            context_digest=context_digest,
            initiator_ref=initiator_ref,
        )

    # -- 12. admission (structural only) --------------------------------------------------
    digests = seal(plan, registry_digest)
    approvals: list[StepApproval] = []
    separation: set[str] = set()

    for index in mutating:
        definition = definitions[index]

        if definition.approval_policy.kind is ApprovalKind.PER_STEP_APPROVAL:
            approvals.append(
                StepApproval(
                    step_key=steps[index]["step_key"],
                    capability_id=definition.capability_id,
                    approver_permission=(
                        definition.approval_policy.approver_permission
                    ),
                    corridor=definition.corridor,
                )
            )

        separation.update(definition.separation_requirements)

    return PlanAdmission(
        decision=(
            AdmissionDecision.ADMITTED_PENDING_STEP_APPROVALS
            if mutating
            else AdmissionDecision.ADMITTED_READ_ONLY
        ),
        violations=(),
        registry_digest=registry_digest,
        context_digest=context_digest,
        execution_digest=digests["execution_digest"],
        presentation_digest=digests["presentation_digest"],
        idempotency_key=derive_idempotency_key(
            plan["objective_ref"],
            plan["plan_version"],
            digests["execution_digest"],
        ),
        objective_ref=plan["objective_ref"],
        plan_version=plan["plan_version"],
        step_keys=tuple(sorted(by_key)),
        mutating_step_keys=tuple(
            sorted(steps[index]["step_key"] for index in mutating)
        ),
        requires_step_approvals=tuple(
            sorted(approvals, key=lambda a: a.step_key)
        ),
        separation_requirements=tuple(sorted(separation)),
        initiator_ref=initiator_ref,
    )
