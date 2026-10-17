from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Final


# INTELLIGENCE-IMPL-0: constante de CODIGO, nao configuracao. Nenhuma
# variavel de ambiente, Settings ou parametro pode alterar este valor;
# ativar dados reais exige novo gate, nova migracao (CHECK
# data_classification) e nova revisao do PO.
REAL_DATA_ALLOWED: Final[bool] = False

# Modos reconhecidos pelo gateway nesta fase. governed/bounded sao
# recusados tambem no gateway (alem do validador de Settings).
SUPPORTED_MODES: Final[frozenset[str]] = frozenset({"off", "shadow"})


class DataClassification(str, Enum):
    SYNTHETIC = "synthetic"
    REAL = "real"


class CallState(str, Enum):
    RESERVED = "reserved"
    IN_FLIGHT = "in_flight"
    SETTLED = "settled"
    UNKNOWN = "unknown"
    RELEASED = "released"


class ReleaseBasis(str, Enum):
    NEVER_SENT = "never_sent"
    PROVIDER_DOCUMENTED = "provider_documented"
    ADMIN_RECONCILIATION = "admin_reconciliation"


class ProviderStatus(str, Enum):
    COMPLETED = "completed"
    REFUSAL = "refusal"
    TRUNCATED = "truncated"


@dataclass(frozen=True)
class ModelCallRequest:
    """
    Structured-output request. The gateway never executes provider
    tools and never accepts free-form output: the response must be a
    JSON document valid against output_schema.
    """

    call_key: str
    purpose: str
    profile: str
    instructions: str
    input_text: str
    output_schema: Mapping[str, Any]
    max_output_tokens: int
    data_classification: DataClassification = (
        DataClassification.SYNTHETIC
    )
    user_budget_id: int | None = None
    objective_budget_id: int | None = None
    deployment_budget_id: int | None = None
    objective_id: int | None = None
    plan_id: int | None = None
    prompt_template_id: str | None = None
    output_schema_id: str | None = None

    @property
    def budget_ids(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    budget_id
                    for budget_id in (
                        self.user_budget_id,
                        self.objective_budget_id,
                        self.deployment_budget_id,
                    )
                    if budget_id is not None
                }
            )
        )


@dataclass(frozen=True)
class ProviderRequest:
    model_id: str
    instructions: str
    input_text: str
    output_schema: Mapping[str, Any]
    max_output_tokens: int
    timeout_seconds: int


@dataclass(frozen=True)
class ProviderResponse:
    status: ProviderStatus
    output_text: str | None
    input_tokens: int
    output_tokens: int
    request_id: str | None = None


@dataclass(frozen=True)
class ModelCallResult:
    """Outcome of Gateway.call. Never carries prompt text."""

    ok: bool
    parsed: Any
    error_code: str | None
    call_ids: tuple[int, ...]
    final_state: str | None
    attempts: int
    actual_micro: int
    actual_tokens: int
    profiles_used: tuple[str, ...] = field(default_factory=tuple)
