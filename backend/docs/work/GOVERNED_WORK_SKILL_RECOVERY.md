# Governed Work Skill Recovery

**Status:** Commit 24E.3 recovery and observability boundary.

24E.3 adds a non-executing maintenance layer around the durable
`work_skill_executions` ledger.

SkillInvocation stale-running recovery executes first. Work Skill recovery then
calls only `WorkSkillExecutionService.reconcile()` for bounded candidates.
It never receives raw Skill input, never calls dispatch and never executes a
handler.

`retry_required` and `configuration_retry_required` are attention states.
Maintenance does not reconstruct payload or guess input; a trusted caller must
resupply the exact input whose digest matches the durable action before a new
dispatch attempt.

Governed Work dispatch uses a dedicated in-process sliding-window limiter keyed
by a SHA-256 digest of the current authority user ID. It is intentionally
separate from the explicit human Skill API limiter. Distributed deployments
still require shared/gateway enforcement.

Work Skill observability uses a strict safe-field allowlist. Raw input/output,
digests, dispatch/idempotency keys, actor references, credentials and raw
exception text are excluded.

The legacy Orchestrator remains unchanged and has no Work/Approval/Skill
repository or dispatch authority. Its decisions and model/agent context are
intent/context only, never authority. A controlled adapter is deferred until
after the Commit 24 cumulative security gate, in closed-loop intelligence.

`external` Work dispatch remains blocked. Database idempotency alone does not
establish exactly-once semantics for external business side effects.

## 9. Shutdown drain semantics

The asynchronous recovery boundary protects its `asyncio.to_thread()` worker
with `asyncio.shield()`.

If application shutdown cancels the surrounding asyncio task while a recovery
cycle is in flight, cancellation is not considered complete until the
synchronous worker finishes and closes its database Session. Only then is
`CancelledError` propagated back to the lifespan shutdown path.

This prevents application shutdown from advancing to engine disposal or a
subsequent test lifecycle while a Work Skill recovery thread can still own a
PostgreSQL Session/transaction.

The shutdown drain does not retry, dispatch or execute a Skill handler. It only
waits for an already-started non-executing reconciliation cycle to finish.
