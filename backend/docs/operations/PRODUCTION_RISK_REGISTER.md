# Production Risk Register

**Status:** living risk register from Commit 24D onward.

| Risk | Current control | Residual risk | Becomes blocker |
| --- | --- | --- | --- |
| Thread timeout is not hard kill | 24E.1 governed autonomy uses a killable child-process boundary; explicit human Skill execution remains on the bounded thread executor | explicit human timeout still cannot hard-kill its thread; process kill cannot undo already-completed side effects | Production Pilot before untrusted handlers; explicit path isolation remains hardening |
| Untrusted/plugin code isolation | 24E.1 isolates trusted autonomous internal_python handlers in a killable process; plugin/untrusted handlers remain blocked from governed autonomy | process boundary is not a security sandbox and explicit plugin runtime remains in-process | Production Pilot before untrusted handlers |
| External side effect after crash | invocation ledger, idempotency, one-time Approval consumption | handler may have produced external effect before crash is observed | 24E/Pilot before autonomous external effects |
| Distributed rate limiting | current per-user limiter is in-process | limits are not global across instances | Production Readiness/Pilot |
| Approval/consumption recovery | durable approval + consumption + runtime idempotency | distributed maintenance/reconciliation still required | 24E |
| Secrets management | config validation, redaction, API-key hardening | production secret lifecycle/rotation not fully externalized | Production Readiness |
| PostgreSQL backup/restore | database safety guards and migrations | restore procedure still needs tested operational evidence | Production Pilot |
| Distributed observability | structured logs/request IDs | centralized retention/alerting/SLOs not complete | Production Readiness |
| Autonomous Work/Orchestrator authority | not integrated in 24D | future scheduler could become an authority bypass | 24E before enabling integration |

## 24D decision

These risks do not block 24D as an internal foundation because 24D adds no public
route, no Work/Orchestrator autonomous integration and no Skill selection. The
thread timeout limitation is handled by restricting governed autonomy to known
`internal_python` handlers explicitly trusted by server configuration.

## Promotion rule

A risk may move to a later stage only with an explicit documented control and a
Gate assertion. “Known risk” is not equivalent to “accepted for production”.

## 24E.2 Work execution linkage

24E.2 adds a durable, server-authoritative Work-to-Skill execution ledger.
`context_data`, Work origin fields, model output and agent bindings remain data,
never authority. The current authority user and exact Skill scope are
re-authorized before each new dispatch.

The ledger persists only the input digest, not raw input. A trusted internal
caller must re-supply the exact input for dispatch/retry and the digest must
match.

External Work dispatch remains blocked. The existing per-user Skill limiter is
reused against the authority user but remains in-process; distributed abuse
control is still a Production Readiness item. Work/Skill reconciliation is
durable, but external side effects are not exactly-once.

## Commit 24E.3 recovery and adapter decision

- Work Skill recovery is non-executing and follows SkillInvocation recovery.
- `retry_required` and `configuration_retry_required` are operator-attention
  states; maintenance never reconstructs payload or replays a handler.
- Autonomous Work dispatch has a dedicated in-process authority-user limiter.
  Shared/distributed rate limiting remains a Production Readiness blocker.
- Structured Work Skill logs use a safe metadata allowlist; centralized
  retention, alerting and SLOs remain Production Readiness work.
- The legacy Orchestrator receives no Work/Approval/Skill repository or
  dispatch authority in Commit 24. The controlled adapter is deferred to
  closed-loop intelligence after the cumulative Commit 24 security gate.
- `external` Work dispatch remains blocked until provider/business
  reconciliation can resolve unknown outcomes and side effects.

## Commit 25A Outcome Evaluation Foundation

- Outcome learning starts only after a durable terminal `WorkSkillExecution`;
  it cannot grant authority, retry, replan or dispatch a Skill.
- Outcome Memory stores deterministic bounded metadata only. Raw input, raw
  output, raw error/exception text, credentials, dispatch keys, approval
  payloads and actor references are excluded from learning persistence.
- `MemoryService` and `WorkManagerService` retain their existing commit
  ownership, so 25A uses a recoverable state machine rather than pretending
  that Memory and Work linking are one atomic transaction.
- Recovery prechecks the exact `WorkMemoryLink(relation=outcome)` before
  calling `link_memory()`. This avoids an idempotency fingerprint conflict
  after a crash where the Work link committed but evaluation finalization did
  not.
- Outcome maintenance can only materialize ledger/Memory/Work-link state and
  never invokes `dispatch`, governed execution, runtime handlers or the legacy
  Orchestrator.
- The legacy Orchestrator remains outside the first closed-loop APPLY. Public
  API/OpenAPI and the external Work execution block remain unchanged.

## Commit 25B authorized learning context

- The first 25B APPLY exposes no public endpoint and injects no learning data
  into Skill runtime. `input_payload` identity, Approval binding and handler
  invocation remain unchanged.
- Prior outcomes are eligible only when the completed deterministic 25A ledger,
  active outcome Memory, exact Work scope, same Skill version and
  `WorkMemoryLink(relation=outcome)` all agree.
- The resolver reloads the current active authority User and independently
  rechecks Work-read and Memory-read authorization. Caller-supplied role/scope
  and Memory/model content cannot grant authority.
- Returned context is a bounded metadata allowlist. Raw input/output/error,
  Memory title/content/context/evidence, credentials, approval payloads and
  actor references remain excluded.
- Runtime context injection, model ranking, automatic action selection,
  retry/replan and the legacy Orchestrator adapter remain deferred and require
  separate threat-model and Gate evidence.
