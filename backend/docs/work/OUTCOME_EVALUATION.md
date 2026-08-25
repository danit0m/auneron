# Work Outcome Evaluation

## Purpose

Commit 25A introduces the first closed-loop learning foundation after a
`WorkSkillExecution` has already reached an immutable terminal result. The
component records deterministic learning evidence; it does not select, retry,
dispatch or execute Skills.

## Deterministic mapping

| WorkSkillExecution status | evaluation_code | learning_signal |
| --- | --- | --- |
| `succeeded` | `execution_succeeded` | `positive` |
| `failed` | `execution_failed` | `negative` |
| `timed_out` | `execution_timed_out` | `negative` |
| `cancelled` | `execution_cancelled` | `neutral` |

The evaluator version is `deterministic_v1`. No model scores outcome quality in
25A.

## Durable state machine

1. The existing Work Skill terminal transaction commits first.
2. `WorkOutcomeEvaluation` is created or resumed as `pending`.
3. `MemoryService.remember()` creates an `observation` plus `supports`
   evidence using a deterministic `memory_key`.
4. The evaluation records `memory_item_id` and becomes `memory_recorded`.
5. The current Work row is locked and the exact `WorkMemoryLink` with
   `relation=outcome` is checked before any creation attempt.
6. Only after the exact link exists does the evaluation become `completed`.
7. A post-terminal failure becomes `retry_required` when that safe state can
   be persisted.

The stages intentionally respect the existing commit ownership of
`MemoryService` and `WorkManagerService`; this is a recoverable state machine,
not a distributed transaction.

## Recovery rule for WorkMemoryLink

`WorkManagerService.link_memory()` includes `expected_version` in its
idempotency request fingerprint. Recovery must therefore never blindly replay
the same outcome link request after a crash. It locks the current `WorkItem`,
queries the exact `(work_item_id, memory_id, relation='outcome')` link and calls
`link_memory()` only when the link is absent.

This covers the crash window where the Work link committed but the final
`WorkOutcomeEvaluation.completed` commit did not.

## Memory contract

Outcome memory is intentionally bounded:

- `memory_type=observation`
- `source_type=derived`
- `confidence=1.000`
- deterministic `memory_key=work-skill-outcome:<execution_id>:v1`
- Work link `relation=outcome`
- scope comes only from persisted `WorkItem.scope_type/account_id/subject_user_id`

Raw input, raw output, raw error/exception text, credentials, dispatch keys,
approval payloads and actor references are not persisted as learning content.

## Authority boundary

Outcome Evaluation never calls:

- `WorkSkillExecutionService.dispatch()`
- `GovernedSkillExecutionService.execute()`
- `SkillRuntimeService.invoke()`
- `ExecutionPipeline.execute()`
- `agent.handler()`

Learning and Memory remain context only. RBAC, Approval and autonomy controls
remain authoritative and unchanged. External Work execution remains blocked.
The legacy Orchestrator is not integrated in 25A.

## Maintenance

The maintenance worker reuses the bounded Work Skill recovery interval and
batch settings, owns a fresh `SessionLocal`, and delays cancellation until an
in-flight synchronous DB worker has drained. Maintenance can materialize only
the evaluation ledger, Memory observation/evidence, the Work outcome link and
the existing `memory_linked` Work event.

## Public surface

25A adds no public endpoint and does not change the OpenAPI contract.
