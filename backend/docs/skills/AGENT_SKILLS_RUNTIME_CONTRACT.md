# Agent Skills Runtime Contract V1

**Status:** Finalized through Gate 23E

## 1. Boundary

23C introduces the internal invocation runtime for one exact published
`SkillVersion`. 23D keeps that runtime authority-free and places an
authenticated RBAC/scope boundary in front of it for explicit user-initiated
HTTP execution. Approval and autonomous selection remain Commit 24 work.

The runtime receives:

- an exact `skill_version_id`;
- independently supplied actor attribution;
- a JSON-compatible input payload;
- an optional idempotency key for read-only execution;
- a mandatory idempotency key for `mutating` and `external` execution.

## 2. Handler allowlist

Persisted `handler_reference` values never trigger arbitrary import.

`SkillHandlerRegistry` is an explicit in-process allowlist keyed by the pair
`(runtime_kind, handler_reference)`. A published version executes only when an
exact callable was registered by trusted application bootstrap code.

This rule applies to both `internal_python` and `plugin` runtime kinds.
A plugin reference therefore requires an explicitly registered adapter and
cannot load a package, path or network endpoint merely because the catalog
contains its name.

The legacy `AgentRegistry` and `EventBus` are unchanged by 23C.

## 3. Input and output contracts

23C validates JSON-compatible values using JSON Schema Draft 2020-12.

Before handler execution the runtime:

1. canonicalizes JSON with sorted keys, compact separators and UTF-8;
2. rejects NaN, infinity, cyclic values and non-string object keys;
3. limits input to 64 KiB and JSON depth to 10;
4. rejects non-local `$ref` values so schema validation cannot fetch remote
   resources;
5. validates the input against the immutable published `input_schema`.

After handler execution the runtime:

1. canonicalizes the output;
2. enforces the published `max_output_bytes`;
3. validates the output against the immutable published `output_schema`;
4. stores a successful output envelope for exact idempotent replay.

Invalid input is `rejected`. Invalid handler output is `failed`.

## 4. Execution limits

`BoundedSkillExecutor` uses a fixed worker pool plus a `BoundedSemaphore`.
Tasks are not submitted after every runtime slot is occupied, preventing an
unbounded executor queue.

The published `timeout_seconds` limits how long the caller waits for a handler.
A timed-out invocation is finalized as `timed_out`. The semaphore slot remains
occupied until the underlying callable actually exits.

Python cannot safely kill a thread that has already begun executing. Gate 23E
therefore does not misrepresent timeout as cancellation or side-effect rollback.
The timed-out callable keeps its bounded executor slot until it really exits,
preventing timeout storms from creating an unbounded queue.

Commit 23 permits only trusted, explicitly allowlisted in-process handlers.
Untrusted plugin/process isolation is not provided by this runtime and must be
implemented as a separate execution boundary before untrusted code is enabled.
Mutating and external execution keep the mandatory idempotency requirement.

## 5. Invocation ledger

`skill_invocations` is append-preserving execution history tied to an exact
`skill_version_id`.

The ledger stores:

- actor type/reference and nullable user attribution;
- optional normalized idempotency key;
- request fingerprint and input digest, but not the raw input payload;
- lifecycle status;
- successful output envelope, digest and byte size;
- sanitized terminal error code;
- start/finish timestamps and duration.

Raw exception text, traceback, credentials and raw input are not persisted by
the runtime ledger.

Version deletion is `RESTRICT` while invocation history exists. User deletion
sets `actor_user_id` to null while preserving `actor_reference`.

## 6. Idempotency

The database uniqueness boundary is:

```text
skill_version_id
+ actor_type
+ actor_reference
+ idempotency_key
```

A repeated key with the same canonical request fingerprint never executes the
handler again:

- `succeeded` replays the stored output;
- `running` reports an in-progress conflict;
- a terminal failure replays a sanitized domain failure.

Reusing the key with a different request fingerprint raises an idempotency
conflict.

The runtime commits the `running` ledger row before submitting the handler.
That database uniqueness boundary prevents concurrent duplicate execution for
the same idempotency scope.

## 7. Sanitized outcomes

Terminal error codes are bounded internal values such as:

- `input_validation_failed`;
- `input_schema_invalid`;
- `handler_not_allowed`;
- `runtime_busy`;
- `timeout`;
- `execution_failed`;
- `output_limit_or_json_invalid`;
- `output_validation_failed`;
- `output_schema_invalid`.

Handler exception messages are converted to `SkillExecutionError` and are not
copied into the ledger or returned by the runtime service.

## 8. Transaction ownership

`SkillRepository` continues to execute statements and `flush` only.

`SkillRuntimeService` owns invocation transactions:

1. create and commit the `running` ledger row;
2. perform validation and bounded handler execution outside an open database
   transaction;
3. lock the ledger row and commit exactly one terminal state.

A process crash can leave a `running` row behind. Gate 23E recovers only rows
older than the configured stale threshold (minimum 301 seconds, above the
maximum published timeout). Recovery locks rows with `FOR UPDATE SKIP LOCKED`,
marks them `failed` with the sanitized code `stale_running_recovered`, and never
re-executes the handler. The service owns the recovery transaction.

## 9. Gate 23D HTTP boundary

23D exposes only
`POST /agent-skills/versions/{version_id}/invoke`.

The route derives a `user` actor from `AuthenticatedSession`; client JSON
cannot supply actor attribution. Authorization resolves the exact published
version and all of its capability declarations before calling this runtime.

For `account` and `user` capabilities, `input_payload.account_id` and
`input_payload.subject_user_id` are reserved top-level authorization fields
passed unchanged to the handler.
The policy authorizes those values before the runtime sees them. Supplying a
reserved field without the corresponding capability, or omitting one that a
declared scope requires, is rejected.

`mutating` execution requires its dedicated permission. `external` execution
requires its dedicated permission plus a currently elevated session. The
runtime's existing idempotency rule continues to require an idempotency key
for mutating and external calls.

23D intentionally exposes no invocation-history endpoint. The current ledger
does not persist account/user scope identifiers separately, so a public history
API would not have a durable anti-IDOR key independent from successful output.

## 10. Gate 23E operational hardening

23E adds:

- structured runtime lifecycle telemetry correlated with `X-Request-ID` when
  an HTTP context exists;
- no payload, raw input, idempotency key, credential or exception text in the
  Skill telemetry contract;
- a bounded in-memory per-user sliding-window rate limit for the explicit Skill
  API, returning `429` plus `Retry-After`;
- stale-`running` recovery through locked batches and a background maintenance
  loop;
- configurable worker count, rate window, stale threshold, maintenance interval
  and recovery batch size;
- final documentation of the trusted in-process timeout boundary.

The in-process limiter is defense-in-depth for one application process. A
multi-worker/multi-replica deployment must also enforce distributed/edge rate
limits. A thread timeout is still not a hard-kill. Commit 23 therefore does not
authorize untrusted executable plugins.

Commit 24 owns sensitive-action approval, autonomous selection and execution
authority for non-user actors.

## 11. Commit 25C side-band runtime context foundation

25C adds the internal `work_learning_v1` runtime-context protocol without
changing public Skill input. The protocol is side-band because `input_payload`
is already bound into input schema validation, Work/Approval identity and the
runtime input digest.

Contextless execution remains on the existing `handler(payload)` path and keeps
the legacy request fingerprint envelope unchanged. A contextful execution is
allowed only for `internal_python`, `read_only`, `isolated=True` and requires
both the immutable SkillVersion manifest and the trusted server-side registry
to declare `runtime_context_protocol=work_learning_v1`.

The context contains at most 10 deterministic 25A outcome metadata items using
an exact safe field allowlist and a 16384-byte canonical ceiling. The runtime
binds the protocol plus context digest into the request fingerprint while
leaving `input_digest` unchanged. The isolated worker uses a versioned
side-band wire envelope and calls only opted-in handlers as
`handler(payload, runtime_context)`.

The first 25C APPLY contains no Work learning resolver hook, no
`WorkSkillExecutionService` or `GovernedSkillExecutionService` change, no
public API change, no migration and no legacy Orchestrator integration. Durable
production Work-context snapshotting and actual resolver-to-runtime forwarding
remain separately gated.
