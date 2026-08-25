# Work Learning Context

**Status:** Commit 25B first APPLY — internal resolver only.

## Purpose

25B introduces a read-only boundary for retrieving prior deterministic Work
Skill outcomes as **learning context metadata**. It does not execute a Skill,
select an action, modify a Work, mutate Memory, retry, replan or grant
permission.

The first implementation is intentionally not connected to Skill runtime. No
learning data is inserted into `input_payload`, no handler signature changes,
and no public API route is added.

## Authorized resolver

`WorkLearningContextService.resolve()` accepts only:

- a persisted target `work_item_id`;
- a `skill_version_id` used solely as a retrieval discriminator;
- an `authority_user_id` whose current active User row is reloaded;
- a bounded `limit` from 1 through 10;
- an optional timezone-aware `as_of` timestamp.

The caller cannot supply Work scope or role. Scope is derived exclusively from
`WorkItem.scope_type`, `account_id` and `subject_user_id`; role comes from the
current persisted User.

Before querying learning context, the service independently requires current
`read` authorization for both Work and Memory on that exact persisted scope.
Learning, Memory content and model output never grant authority.

## Candidate contract

`WorkLearningContextRepository` is transaction-free and SELECT-only. A
candidate must satisfy every condition below:

- `WorkOutcomeEvaluation.status = completed`;
- evaluator version is `deterministic_v1`;
- the linked `WorkSkillExecution` is terminal;
- exact `skill_version_id` match;
- source Work is not the target Work;
- source Work scope exactly equals the target Work scope;
- linked Memory scope exactly equals the target Work scope;
- Memory is active `observation`, source `derived`, confidence `1.000` and
  importance `0.500`;
- deterministic outcome Memory key/reference match the source execution;
- Memory is valid at `as_of`;
- an exact `WorkMemoryLink(relation=outcome)` exists.

Ordering is deterministic: terminal execution `finished_at DESC`, then
execution `id DESC`. The query overfetches at most one row (`limit + 1`) and
the service returns at most the requested limit.

## Output allowlist

Only these fields leave the resolver:

```text
memory_id
source_work_item_id
work_skill_execution_id
skill_version_id
terminal_status
evaluation_code
learning_signal
observed_at
```

The resolver does not return or consume raw Skill input/output/error,
credentials, approval payloads, actor references, Memory title/content,
Memory `context_data`, or evidence text.

## Transaction and authority boundary

The repository performs no `commit`, `rollback`, `flush`, `FOR UPDATE` or
write. The service performs no Work/Memory mutation and has no dependency on
Skill dispatch, governed execution, runtime invocation, handlers or the legacy
Orchestrator.

The first 25B APPLY therefore adds **retrieval capability, not execution
authority**.

## Explicitly deferred

Later checkpoints must separately design and gate any runtime context channel,
handler envelope change, context snapshot persistence, model ranking,
automatic action selection, automatic retry/replan or legacy Orchestrator
adapter. None is authorized by this document.
