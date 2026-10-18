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
from app.intelligence.planning.authority import SyntheticAuthorityContext
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
from app.intelligence.planning.codes import RejectionCode
from app.intelligence.planning.codes import Violation
from app.intelligence.planning.codes import make_violation
from app.intelligence.planning.dag import analyze_dag
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
    registry: CapabilityRegistry,
    violations: list[Violation],
    *,
    context_digest: str | None = None,
    initiator_ref: str | None = None,
) -> PlanAdmission:
    return PlanAdmission(
        decision=AdmissionDecision.REJECTED,
        violations=tuple(sorted(set(violations))),
        registry_digest=registry.digest,
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
    """
    if proposer_kind not in SUPPORTED_PROPOSER_KINDS:
        raise ValueError("Unsupported proposer_kind in this phase.")

    limits = registry.limits

    # -- 0. registry integrity ------------------------------------------
    if registry.digest != pinned_registry_digest:
        return _reject(
            registry,
            [make_violation(RejectionCode.REGISTRY_INTEGRITY_FAILURE)],
        )

    # -- 1. input ----------------------------------------------------------
    try:
        plan = strict_parse(raw, limits)
    except InputError as error:
        return _reject(registry, [make_violation(error.code)])

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
        return _reject(registry, found)

    # -- 3. value rules and closed structure ---------------------------
    found, json_depth = scan_value(plan, limits)

    if found:
        return _reject(registry, found)

    errors = sorted(
        _SKELETON_VALIDATOR.iter_errors(plan),
        key=lambda e: (_json_path(e.absolute_path), e.validator),
    )

    if errors:
        return _reject(
            registry,
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
        return _reject(registry, found)

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
        return _reject(registry, found)

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
        return _reject(registry, found)

    # -- 7. DAG ------------------------------------------------------------
    dag = analyze_dag(steps, limits)

    if dag.violations:
        return _reject(registry, list(dag.violations))

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
        return _reject(registry, found)

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
        return _reject(registry, found)

    # -- 10. authority (synthetic context) ----------------------------------------
    if not isinstance(authority, SyntheticAuthorityContext):
        return _reject(
            registry,
            [make_violation(RejectionCode.AUTHORITY_CONTEXT_INVALID)],
        )

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
            registry,
            found,
            context_digest=authority.context_digest(),
            initiator_ref=authority.initiator_ref,
        )

    # -- 11. policy reconciliation -----------------------------------------------------
    if not isinstance(policy, PolicyReference):
        return _reject(
            registry,
            [make_violation(RejectionCode.POLICY_REFERENCE_MISSING)],
            context_digest=authority.context_digest(),
            initiator_ref=authority.initiator_ref,
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
            registry,
            found,
            context_digest=authority.context_digest(),
            initiator_ref=authority.initiator_ref,
        )

    # -- 12. admission (structural only) --------------------------------------------------
    digests = seal(plan, registry.digest)
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
        registry_digest=registry.digest,
        context_digest=authority.context_digest(),
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
        initiator_ref=authority.initiator_ref,
    )
