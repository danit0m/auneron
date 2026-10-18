from dataclasses import dataclass
from enum import Enum
from enum import IntEnum


class Stage(IntEnum):
    """Fixed validation order. Earlier stages win: the first stage with
    violations ends the run (fail-fast between stages)."""

    REGISTRY_INTEGRITY = 0
    INPUT = 1
    CONTRACT = 2
    SCHEMA = 3
    LIMITS = 4
    CAPABILITIES = 5
    PARAMETERS = 6
    DAG = 7
    EFFECTS = 8
    DATA = 9
    AUTHORITY = 10
    POLICY = 11


class RejectionCode(str, Enum):
    # 0 -- registry integrity
    REGISTRY_INTEGRITY_FAILURE = "registry_integrity_failure"
    # 1 -- input
    INPUT_NOT_UTF8 = "input_not_utf8"
    INPUT_TOO_LARGE = "input_too_large"
    INPUT_NOT_JSON = "input_not_json"
    INPUT_NOT_OBJECT = "input_not_object"
    INPUT_DUPLICATE_KEY = "input_duplicate_key"
    INPUT_NON_FINITE_NUMBER = "input_non_finite_number"
    INPUT_NESTING_TOO_DEEP = "input_nesting_too_deep"
    # 2 -- contract
    CONTRACT_UNKNOWN = "contract_unknown"
    CONTRACT_VERSION_UNSUPPORTED = "contract_version_unsupported"
    REGISTRY_VERSION_MISMATCH = "registry_version_mismatch"
    SERVER_FIELD_FORBIDDEN = "server_field_forbidden"
    # 3 -- schema and value rules
    SCHEMA_VIOLATION = "schema_violation"
    VALUE_NULL_FORBIDDEN = "value_null_forbidden"
    VALUE_FLOAT_FORBIDDEN = "value_float_forbidden"
    INTEGER_OUT_OF_RANGE = "integer_out_of_range"
    STRING_NOT_NFC = "string_not_nfc"
    STRING_CONTROL_CHARACTER = "string_control_character"
    STRING_ENCODING_INVALID = "string_encoding_invalid"
    # 4 -- limits
    LIMIT_STEPS = "limit_steps"
    LIMIT_JSON_DEPTH = "limit_json_depth"
    LIMIT_DEPENDENCIES = "limit_dependencies"
    LIMIT_SUMMARY_LENGTH = "limit_summary_length"
    LIMIT_RATIONALE_LENGTH = "limit_rationale_length"
    LIMIT_PREDICATES = "limit_predicates"
    # 5 -- capabilities
    CAPABILITY_UNKNOWN = "capability_unknown"
    CAPABILITY_VERSION_UNSUPPORTED = "capability_version_unsupported"
    CAPABILITY_DISABLED = "capability_disabled"
    CAPABILITY_NOT_LLM_PROPOSABLE = "capability_not_llm_proposable"
    # 6 -- parameters and predicates
    PARAMS_INVALID = "params_invalid"
    PARAMS_SEMANTIC_INVALID = "params_semantic_invalid"
    PREDICATE_INVALID = "predicate_invalid"
    # 7 -- DAG
    DAG_DUPLICATE_STEP_KEY = "dag_duplicate_step_key"
    DAG_SELF_DEPENDENCY = "dag_self_dependency"
    DAG_DUPLICATE_DEPENDENCY = "dag_duplicate_dependency"
    DAG_UNKNOWN_DEPENDENCY = "dag_unknown_dependency"
    DAG_CYCLE = "dag_cycle"
    DAG_DEPTH_EXCEEDED = "dag_depth_exceeded"
    DAG_FAN_OUT_EXCEEDED = "dag_fan_out_exceeded"
    # 8 -- effects
    EFFECT_MUTATING_LIMIT = "effect_mutating_limit"
    EFFECT_DATAFLOW_FORBIDDEN = "effect_dataflow_forbidden"
    EFFECT_ELIGIBILITY_DEPENDENCY_MISSING = (
        "effect_eligibility_dependency_missing"
    )
    EFFECT_ELIGIBILITY_PRECONDITION_MISSING = (
        "effect_eligibility_precondition_missing"
    )
    # 9 -- data policy
    DATA_REAL_DATA_REFUSED = "data_real_data_refused"
    DATA_TOKEN_LIKE_VALUE = "data_token_like_value"
    # 10 -- authority (synthetic context)
    AUTHORITY_CONTEXT_INVALID = "authority_context_invalid"
    AUTHORITY_PERMISSION_MISSING = "authority_permission_missing"
    AUTHORITY_SCOPE_INACCESSIBLE = "authority_scope_inaccessible"
    # 11 -- policy reconciliation
    POLICY_REFERENCE_MISSING = "policy_reference_missing"
    POLICY_REFERENCE_MISMATCH = "policy_reference_mismatch"
    # seal verification (outside the stage pipeline)
    DIGEST_MISMATCH = "digest_mismatch"


STAGE_OF: dict[RejectionCode, Stage] = {
    RejectionCode.REGISTRY_INTEGRITY_FAILURE: Stage.REGISTRY_INTEGRITY,
    RejectionCode.INPUT_NOT_UTF8: Stage.INPUT,
    RejectionCode.INPUT_TOO_LARGE: Stage.INPUT,
    RejectionCode.INPUT_NOT_JSON: Stage.INPUT,
    RejectionCode.INPUT_NOT_OBJECT: Stage.INPUT,
    RejectionCode.INPUT_DUPLICATE_KEY: Stage.INPUT,
    RejectionCode.INPUT_NON_FINITE_NUMBER: Stage.INPUT,
    RejectionCode.INPUT_NESTING_TOO_DEEP: Stage.INPUT,
    RejectionCode.CONTRACT_UNKNOWN: Stage.CONTRACT,
    RejectionCode.CONTRACT_VERSION_UNSUPPORTED: Stage.CONTRACT,
    RejectionCode.REGISTRY_VERSION_MISMATCH: Stage.CONTRACT,
    RejectionCode.SERVER_FIELD_FORBIDDEN: Stage.CONTRACT,
    RejectionCode.SCHEMA_VIOLATION: Stage.SCHEMA,
    RejectionCode.VALUE_NULL_FORBIDDEN: Stage.SCHEMA,
    RejectionCode.VALUE_FLOAT_FORBIDDEN: Stage.SCHEMA,
    RejectionCode.INTEGER_OUT_OF_RANGE: Stage.SCHEMA,
    RejectionCode.STRING_NOT_NFC: Stage.SCHEMA,
    RejectionCode.STRING_CONTROL_CHARACTER: Stage.SCHEMA,
    RejectionCode.STRING_ENCODING_INVALID: Stage.SCHEMA,
    RejectionCode.LIMIT_STEPS: Stage.LIMITS,
    RejectionCode.LIMIT_JSON_DEPTH: Stage.LIMITS,
    RejectionCode.LIMIT_DEPENDENCIES: Stage.LIMITS,
    RejectionCode.LIMIT_SUMMARY_LENGTH: Stage.LIMITS,
    RejectionCode.LIMIT_RATIONALE_LENGTH: Stage.LIMITS,
    RejectionCode.LIMIT_PREDICATES: Stage.LIMITS,
    RejectionCode.CAPABILITY_UNKNOWN: Stage.CAPABILITIES,
    RejectionCode.CAPABILITY_VERSION_UNSUPPORTED: Stage.CAPABILITIES,
    RejectionCode.CAPABILITY_DISABLED: Stage.CAPABILITIES,
    RejectionCode.CAPABILITY_NOT_LLM_PROPOSABLE: Stage.CAPABILITIES,
    RejectionCode.PARAMS_INVALID: Stage.PARAMETERS,
    RejectionCode.PARAMS_SEMANTIC_INVALID: Stage.PARAMETERS,
    RejectionCode.PREDICATE_INVALID: Stage.PARAMETERS,
    RejectionCode.DAG_DUPLICATE_STEP_KEY: Stage.DAG,
    RejectionCode.DAG_SELF_DEPENDENCY: Stage.DAG,
    RejectionCode.DAG_DUPLICATE_DEPENDENCY: Stage.DAG,
    RejectionCode.DAG_UNKNOWN_DEPENDENCY: Stage.DAG,
    RejectionCode.DAG_CYCLE: Stage.DAG,
    RejectionCode.DAG_DEPTH_EXCEEDED: Stage.DAG,
    RejectionCode.DAG_FAN_OUT_EXCEEDED: Stage.DAG,
    RejectionCode.EFFECT_MUTATING_LIMIT: Stage.EFFECTS,
    RejectionCode.EFFECT_DATAFLOW_FORBIDDEN: Stage.EFFECTS,
    RejectionCode.EFFECT_ELIGIBILITY_DEPENDENCY_MISSING: Stage.EFFECTS,
    RejectionCode.EFFECT_ELIGIBILITY_PRECONDITION_MISSING: Stage.EFFECTS,
    RejectionCode.DATA_REAL_DATA_REFUSED: Stage.DATA,
    RejectionCode.DATA_TOKEN_LIKE_VALUE: Stage.DATA,
    RejectionCode.AUTHORITY_CONTEXT_INVALID: Stage.AUTHORITY,
    RejectionCode.AUTHORITY_PERMISSION_MISSING: Stage.AUTHORITY,
    RejectionCode.AUTHORITY_SCOPE_INACCESSIBLE: Stage.AUTHORITY,
    RejectionCode.POLICY_REFERENCE_MISSING: Stage.POLICY,
    RejectionCode.POLICY_REFERENCE_MISMATCH: Stage.POLICY,
    # DIGEST_MISMATCH belongs to verify_seal, not to the pipeline; it is
    # mapped to the last stage only so every code has a stage.
    RejectionCode.DIGEST_MISMATCH: Stage.POLICY,
}


@dataclass(frozen=True, order=True)
class Violation:
    """
    One deterministic rejection reason. `path` is a JSON path inside the
    plan; `detail` is a short FIXED token (a schema keyword or a policy
    mismatch kind) -- never an input value, so a violation can be logged
    and returned without leaking prompt or parameter content.
    """

    stage: int
    code: str
    path: str
    detail: str = ""

    def __str__(self) -> str:
        suffix = f" [{self.detail}]" if self.detail else ""
        return f"{self.code}@{self.path}{suffix}"


def make_violation(
    code: RejectionCode,
    path: str = "$",
    detail: str = "",
) -> Violation:
    return Violation(
        stage=int(STAGE_OF[code]),
        code=code.value,
        path=path,
        detail=detail,
    )
