# Governed planning foundation (INT-IMPL-1)

Local, deterministic and inert. This package decides **only** whether a
plan proposed by a model is *structurally admissible* against a closed,
code-defined capability registry. It has **no executor**, no provider
call, no route, no permission, no table, no migration and no import by
`app/main.py`. An admission is **not an authorisation**: it is never an
approval, a token or a permission to run anything.

Everything here is `app/intelligence/planning/`, next to the gateway
foundation, and uses only the standard library, `jsonschema` and two
gateway modules (`contracts`, `profiles`).

## Layout

| Module | Role |
|---|---|
| `limits.py` | Frozen `PlanLimits`. |
| `codes.py` | Fixed stage order, stable `RejectionCode` strings, ordered `Violation`. |
| `canonical.py` | Strict parsing, value rules, canonical bytes, domain-separated digests, seal, derived idempotency key. |
| `capabilities.py` | Immutable, versioned `CapabilityRegistry` (`capreg.v1`) and its pinned digest. |
| `schema.py` | The `plan.v1` contract, predicate vocabulary, generated schema. |
| `dag.py` | Deterministic DAG analysis. |
| `authority.py` | `SyntheticAuthorityContext` (laboratory only). |
| `policy.py` | Injected `PolicyReference` and fail-closed reconciliation. |
| `admission.py` | Immutable, non-executable `PlanAdmission`. |
| `validator.py` | `validate_plan(...)`, stages 0-12. |

## Capability registry (`capreg.v1`)

The registry is built once from code. The model has no path to add,
enable or alter a capability. Each definition carries `capability_id`,
`version`, `registered`, `enabled`, `llm_proposable`, `executable`,
`effect_class`, `input_schema`, `output_schema`, `required_permissions`
and `approval_policy`, plus `reference_key`, `corridor`,
`separation_requirements` and `prerequisites`.

| Capability | Effect | Proposable | Notes |
|---|---|---|---|
| `account.get_context` | READ | yes | |
| `nba.evaluate` | READ | yes | eligibility evidence |
| `action_space.evaluate` | READ | yes | eligibility evidence |
| `memory.recall` | READ | yes | needs `memory:read` |
| `account.mark_overdue` | MUTATE | yes | per-step approval (`approval:decide`); structural prerequisite below |
| `work.escalate_to_human` | MUTATE | **no** | `llm_proposable=False` |
| `account.mark_paid` | MUTATE | **no** | `enabled=False` |

Every capability has `executable=False`; the constructor refuses
anything else and a code constant (`EXECUTION_ENABLED_IN_THIS_PHASE`)
is `False`. The registry digest is pinned in code
(`PINNED_REGISTRY_DIGEST`): any change to a capability, schema, flag or
limit changes the digest and the validator rejects **every** plan until
a reviewed change updates the pin together with its tests.

## The `plan.v1` contract

A closed JSON object: `contract`, `registry_version`, `objective_ref`,
`plan_version`, `summary`, `steps`. A step is `step_key`,
`capability_id`, `capability_version`, `params`, `depends_on`,
`preconditions`, `expected_outcomes`, `on_failure` (`abort` or
`escalate`) and `rationale`. References are opaque (`acct_...`,
`obj_...`). There is **no dataflow** between steps: a step cannot consume
another step's output, and template-like text in a parameter is
rejected. Digests are server-owned: a model that sends one is rejected.

| Limit | Value |
|---|---|
| steps | 8 |
| dependency depth (nodes) | 4 |
| fan-out | 3 |
| dependencies per step | 3 |
| mutating steps | 1 |
| input bytes | 32768 |
| JSON depth | 10 |
| summary / rationale characters | 280 / 400 |
| preconditions, expected outcomes per step | 4 each |
| integer magnitude | 2^53 - 1 |

## Validation stages

`validate_plan` is fail-fast **between** stages and collects **all**
violations **within** a stage, sorted by `(stage, code, path)`. A
rejection therefore reports one stage. Violations carry a stable code,
a JSON path and a fixed-vocabulary detail; they never echo input values.

| Stage | Name | Codes |
|---|---|---|
| 0 | registry integrity | `registry_integrity_failure` |
| 1 | input | `input_not_utf8`, `input_too_large`, `input_not_json`, `input_not_object`, `input_duplicate_key`, `input_non_finite_number`, `input_nesting_too_deep` |
| 2 | contract | `contract_unknown`, `contract_version_unsupported`, `registry_version_mismatch`, `server_field_forbidden` |
| 3 | schema and values | `schema_violation`, `value_null_forbidden`, `value_float_forbidden`, `integer_out_of_range`, `string_not_nfc`, `string_control_character`, `string_encoding_invalid` |
| 4 | limits | `limit_steps`, `limit_json_depth`, `limit_dependencies`, `limit_summary_length`, `limit_rationale_length`, `limit_predicates` |
| 5 | capabilities | `capability_unknown`, `capability_version_unsupported`, `capability_disabled`, `capability_not_llm_proposable` |
| 6 | parameters | `params_invalid`, `params_semantic_invalid`, `predicate_invalid` |
| 7 | DAG | `dag_duplicate_step_key`, `dag_self_dependency`, `dag_duplicate_dependency`, `dag_unknown_dependency`, `dag_cycle`, `dag_depth_exceeded`, `dag_fan_out_exceeded` |
| 8 | effects | `effect_mutating_limit`, `effect_dataflow_forbidden`, `effect_eligibility_dependency_missing`, `effect_eligibility_precondition_missing` |
| 9 | data policy | `data_real_data_refused`, `data_token_like_value` |
| 10 | authority | `authority_context_invalid`, `authority_permission_missing`, `authority_scope_inaccessible` |
| 11 | policy | `policy_reference_missing`, `policy_reference_mismatch` (`digest_mismatch` is raised by `verify_seal`) |
| 12 | admission | seal, idempotency key, approval descriptions |

Only the `llm` proposer exists in this phase; any other value raises.

## Canonicalization and digests

Parsing is strict: size is checked before decoding, UTF-8 is strict,
duplicate keys, `NaN` and `Infinity` are refused, nesting depth maps to
a typed code. Values: no `null`, no floats (even integral ones), integers
within 2^53 - 1, strings in NFC with no control characters or lone
surrogates. Canonical bytes use the same algorithm as the approval flow
(sorted keys, compact separators, UTF-8 without ASCII escaping).

Digests are SHA-256 over a domain prefix plus canonical bytes
(`execution`, `presentation`, `registry`, `context`, `idempotency`), so
one can never be replayed as another. The **execution digest** covers
everything that changes what would run (contract, registry digest,
objective, plan version, and per step: key, capability, version, params,
dependencies, preconditions, expected outcomes, `on_failure`) and
excludes the summary, rationales and the order of `steps`,
`depends_on` and predicate lists. The **presentation digest** adds the
text a human reads. The derived idempotency key is deterministic; it
prevents nothing by itself, because replay protection needs
persistence, which does not exist here.

## Authority, identities and policy

`SyntheticAuthorityContext` is plain, laboratory-only data (initiator,
role, permissions, per-account read/manage scope). It consults no RBAC,
database or session, and **cannot be constructed in production**.
Missing and inaccessible accounts return the same code so existence is
not revealed.

Four identities stay distinct: the initiator **I** (on whose behalf the
plan is proposed), the requester/proposer **R** (the model), the decider
**D** (who approves later) and the executor **X** (who would run it).
`I != D` is **declared** by the capability as a separation requirement
and recorded in the admission; this package does **not** enforce it. The
current approval flow does not guarantee it for agent-originated
approvals, so enforcing it is a requirement of a future execution gate.

`PolicyReference` is an injected snapshot of the existing sources of
truth (`ACTION_METADATA`, the autonomy policy, the RBAC vocabulary and
the permissions of the existing read routes). The package never imports
those modules. For each capability **used by a plan** the registry is
reconciled with the snapshot; any disagreement is
`policy_reference_mismatch`, never the more permissive reading, and a
missing reference is `policy_reference_missing`. `llm_proposable ==
(initiation_actor == "agent")` applies only to governed capabilities
that have an `ACTION_METADATA` entry; reads are checked against the
route permissions and the autonomy policy instead.

## Eligibility is structural only

`account.mark_overdue` is admissible only if it has, among its
transitive DAG ancestors, an `action_space.evaluate` or `nba.evaluate`
step with the **same** `account_ref` and `due_date`, **and** carries the
precondition `episode.eligible_mark_overdue eq true`. That proves a
dependency and a stated precondition, never that the account is
eligible: `eligibility_verified` is **always** `False`.

## The admission result

`PlanAdmission` is frozen. Decisions: `rejected`, `admitted_read_only`,
`admitted_pending_step_approvals`. It has no execution method;
`is_grant` is always `False`; `bool(admission)` raises `TypeError`;
`eligibility_verified` is always `False`;
`execution_blocked_reason` is `no_executor_in_this_phase`. Approval
stays **per step** through the existing flow (`ApprovalRequest` has a
single `skill_execution` type): `requires_step_approvals` only
*describes* what each mutating step would need. `audit_view()` is
content-free (codes, counts, digests).

## What this is not

No executor, no approval, no policy decision about a real account, no
LLM call, no provider, no real financial data (`REAL_DATA_ALLOWED` stays
`False`), no new route, permission, table or migration. A later
checkpoint, with its own authorisation, would have to add the real
authority context, the real eligibility evaluation, `I != D`
enforcement, persistence with replay protection and an executor.

## Residual risks

- Secret-like detection is a structural heuristic; the closed schemas
  leave little free text, but it is not a data-loss-prevention control.
- Eligibility, scope and permissions are checked against a synthetic
  snapshot; real evaluation belongs to the execution gate.
- The registry pin protects against silent drift, not against a
  reviewed-but-wrong change.
- The model-facing schema is generated but no provider projection
  exists yet; provider-specific schema subsets are deferred.

## Tests

`tests/test_intelligence_plan_capabilities.py`, `_canonical.py`,
`_schema.py`, `_dag.py`, `_validator.py`, `_authority.py`,
`_policy_reference.py`, `_security.py` and `_boundaries.py`, plus the
exact file lists in `tests/test_intelligence_boundaries.py`. Run them
through the standard backend procedure (`scripts/test.ps1`).
