# Agent Skills Runtime Context Protocol V1

**Status:** 25C protocol foundation with the 25D governed Work production hook.

## Purpose

25C introduces an internal side-band protocol that allows a future governed
Work execution to supply already-authorized, bounded learning metadata to a
trusted isolated Skill handler without changing `input_payload`.

The first protocol is:

```text
work_learning_v1
```

The first 25C APPLY does **not** call `WorkLearningContextService`, does not
change `WorkSkillExecutionService` or `GovernedSkillExecutionService`, and does
not inject context into production Work dispatch. It creates only the runtime
transport and validation boundary needed by a later separately gated hookup.

## Why context is side-band

`input_payload` is already part of several security identities:

- published Skill input-schema validation;
- Work Skill `input_digest`;
- Approval input identity;
- reserved account/user scope authorization fields;
- runtime request fingerprint;
- the existing handler input contract.

Learning context therefore must not be merged into, wrapped around or otherwise
mutate `input_payload`.

## Safe metadata contract

`work_learning_v1` is an object with exactly two fields:

```text
protocol
items
```

`protocol` must equal `work_learning_v1`. `items` contains at most 10 entries.
Each entry contains exactly:

- `memory_id`
- `source_work_item_id`
- `work_skill_execution_id`
- `skill_version_id`
- `terminal_status`
- `evaluation_code`
- `learning_signal`
- `observed_at`

The runtime rejects unknown item fields. It also enforces the exact 25A
terminal mapping:

```text
succeeded -> execution_succeeded -> positive
failed -> execution_failed -> negative
timed_out -> execution_timed_out -> negative
cancelled -> execution_cancelled -> neutral
```

`observed_at` must be timezone-aware and is canonicalized to UTC. The compact,
sorted-key UTF-8 context is capped at 16384 bytes and hashed with SHA-256.

Raw input, raw output, raw error/exception data, Memory content/context/evidence,
credentials, Approval payloads, actor references, roles and scope authority are
not part of this protocol.

## Dual opt-in

Supplying context requires two independent internal declarations:

1. the immutable published `SkillVersion.manifest` contains
   `runtime_context_protocol=work_learning_v1`;
2. the trusted `SkillHandlerRegistry` registration declares the same protocol.

The registry declaration is accepted only for a trusted `internal_python`
handler with an isolated autonomy entrypoint.

A contextful invocation is additionally restricted to:

```text
runtime_kind = internal_python
execution_mode = read_only
isolated = true
```

The context cannot choose a Skill, change a role, change scope or grant any
execution authority.

## Handler contracts

Legacy handlers remain unchanged:

```python
handler(payload)
```

Only a contextful, dual-opted-in isolated invocation uses:

```python
handler(payload, runtime_context)
```

The child-process worker selects the contextful wire protocol out-of-band from
a trusted process argument. For a contextless invocation the existing three
worker arguments and raw input payload wire format remain unchanged.

## Idempotency

Contextless request fingerprint construction remains legacy-compatible: no
`null` context key is added.

For a contextful invocation the request fingerprint additionally binds:

```text
runtime_context.protocol
runtime_context.digest
```

`input_digest` remains the digest of `input_payload` only. Reusing one runtime
idempotency key with the same input but a different learning context therefore
fails as an idempotency conflict before duplicate handler execution.

The first 25C APPLY adds no database column and does not persist the context
payload or context digest separately. Durable Work-context snapshot persistence
belongs to the later production hookup checkpoint.

## Deferred boundaries

The following remain explicitly deferred:

- `WorkLearningContextService -> WorkSkillExecutionService` hookup;
- forwarding through `GovernedSkillExecutionService`;
- durable Work learning-context snapshot persistence;
- mutating or external Skill context;
- public runtime-context API input;
- model ranking or action selection;
- automatic retry/replan;
- legacy Orchestrator integration.

Learning, Memory and model output remain data, never authority.

## 25D production Work hook

25D is the separately gated production hookup deferred by 25C.

Only governed `WorkSkillExecution` read-only actions may supply the protocol.
The immutable SkillVersion manifest and trusted handler registry must both
declare `work_learning_v1`. The Work layer persists one immutable authorized
snapshot per execution before Work starts.

The snapshot service revalidates current Work and Memory read authority on every
access. Retries reuse the exact snapshot and digest rather than re-resolving
learning metadata, preserving the 25C request fingerprint identity.

The public `SkillInvokeRequest` remains `input_payload` only. Direct HTTP callers
cannot inject runtime context. `SkillRuntimeService`, the isolated executor and
the worker retain the 25C transport contract unchanged.
