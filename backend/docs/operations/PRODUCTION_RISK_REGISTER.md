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

## Commit 25C side-band runtime context foundation

- The first 25C APPLY creates only the `work_learning_v1` validation and
  isolated-worker transport protocol. It does not connect the 25B resolver to
  production Work dispatch.
- Learning context never mutates `input_payload`, `input_digest`, Approval
  identity, role or scope authority. Contextful idempotency instead binds a
  separate canonical context digest into the request fingerprint.
- Context requires immutable SkillVersion-manifest opt-in plus trusted registry
  opt-in and is limited to `internal_python`, `read_only`, `isolated=True`.
- The safe context allowlist contains deterministic outcome metadata only; raw
  input/output/error, Memory content/evidence, credentials, Approval payloads
  and actor references remain excluded.
- The first APPLY persists no runtime-context payload or digest column. A later
  production hookup must add durable Work-context snapshot/reconciliation
  evidence before context can affect repeatable autonomous execution.
- Mutating/external learning context, public context input, model-driven action
  selection, automatic retry/replan and legacy Orchestrator integration remain
  blocked behind later gates.

## Commit 25D durable learning-context hookup

25D enables the first production Work learning-context path, but only for
trusted isolated `internal_python` `read_only` Skills with immutable manifest
opt-in and matching server registry opt-in.

Context drift across retries is controlled by one immutable durable snapshot per
`WorkSkillExecution`. Current Work and Memory read authority is revalidated on
every snapshot access, while prior outcomes are not re-resolved on retry.
Snapshot integrity is checked against its canonical digest, item count and byte
count.

Context resolution occurs after current Skill authorization and rate limiting
but before Work starts or dispatch attempts are incremented. An opted-in
context failure has no contextless fallback.

Residual risk remains: snapshots intentionally preserve previously authorized
metadata even if source observations later expire or are superseded. They are
execution evidence/identity, not a live knowledge view, and cannot grant
authority. Mutating/external learning context, automatic action selection,
automatic retry/replan and legacy Orchestrator integration remain deferred.

## Commit 25E legacy autonomy quarantine

25E removes the remaining production-reachable legacy handler execution path
before any closed-loop Orchestrator integration is attempted.

- `EventBus.publish` is observation-only and calls `AIOrchestrator.observe`.
- Decision Engine evaluation, the in-memory DecisionStore and AgentRegistry
  candidate resolution remain available for diagnostics and migration evidence.
- Candidate agent names are advisory metadata and grant no authority.
- `AIOrchestrator.execute`, `ExecutionPipeline.execute` and
  `ExecutionPipeline._execute_agent` fail closed with
  `LegacyAutonomyExecutionBlockedError` before decision-driven legacy execution,
  metrics, telemetry or handler side effects.
- Legacy agent modules remain registered as migration references, but their
  `SessionLocal` / `KnowledgeService.create` side effects are no longer reachable
  from the production EventBus/Orchestrator path.
- No authority user, role or scope is synthesized to bridge the legacy plane.
- Work/Skill/Approval, learning-context snapshots, SkillRuntime and public APIs
  remain unchanged.

Residual risk moves from uncontrolled legacy execution to migration design: a
future Decision-to-Work proposal adapter still needs persisted authority
provenance, exact Skill mapping, idempotency, approval semantics and recovery
before it can be enabled. Automatic Work creation, Skill selection, action
selection and retry/replan remain blocked.

## Commit 25F advisory Skill binding projection

25F reduces Skill-mapping ambiguity without relaxing the 25E quarantine. It
projects advisory legacy agent names to enabled AgentSkillBinding metadata using
SELECT-only repository reads, then filters to published SkillVersions and active
Skills.

The projection is deliberately non-authoritative and non-executable. Binding
configuration, handler references, manifests, capabilities, payloads, authority
identity, Approval state, runtime context and Memory are excluded. There is no
production EventBus hookup, Work creation, Skill execution, Approval mutation,
Memory mutation, public API change or schema migration.

Residual risk remains at the authority boundary: the legacy EventBus still
carries no authorized user principal. A future Decision-to-Work bridge must
persist and revalidate authority provenance, define exact proposal/idempotency
semantics, preserve Approval scope and add crash/reconciliation behavior before
projection metadata may influence action.

## Commit 25G authenticated authority provenance foundation

25G introduces only a minimal immutable server-derived provenance reference for
the authenticated user/session. The reference itself grants no authority, is
not an authorization decision and is not executable intent.

- `authority_user_id` and `auth_session_id` come only from the existing
  `AuthenticatedSession`; the session user id must match the authenticated user.
- Role, permission set, account/user scope, session elevation, Approval state,
  Skill/binding selection, payload, runtime context, Memory, credentials and
  tokens are not copied into provenance.
- A future consumer must reload the current User and AuthSession, validate the
  session is still active, recalculate current RBAC and reauthorize scope and
  the exact Skill. Missing/revoked/expired authority fails closed.
- The first APPLY has no production EventBus hookup, Work creation, Skill
  execution, Approval or Memory mutation, public API, database write or schema
  migration.

Residual risk remains in the deferred bridge: provenance still needs a
production capture point, durable proposal/idempotency identity and
crash/reconciliation design before advisory Orchestrator metadata can influence
Work or governed Skill execution.

## Commit 25H authenticated advisory envelope foundation

25H composes the 25F advisory Skill projection and 25G authority provenance
inside an immutable, non-executable envelope.

- The envelope contains only `decision`, `plan` and `authority`.
- `plan.decision_name` must equal `decision.decision_name`.
- Ordered plan agent names must exactly match `decision.selected_agents`.
- The envelope grants no authority, is not an authorization decision and is
  not executable intent.
- Role, permissions, scope, session elevation, payload, Work, Approval,
  credentials, tokens and Memory are excluded.
- Future consumers must reload current user/session authority and reauthorize
  scope and the exact Skill; invalid or stale authority fails closed.
- The first APPLY has no EventBus/route wiring, database access, Work creation,
  Skill execution, Approval/Memory mutation, public API or schema migration.

Residual risk remains in the deferred bridge: a production capture point,
durable proposal identity, idempotency, crash recovery and reconciliation are
still required before advisory context can influence Work materialization or
governed Skill execution.

## Commit 25I authenticated advisory envelope assembly

25I composes the 25G/25H authority and advisory foundations without enabling a
production bridge.

- Authority is derived from `AuthenticatedSession` only; callers cannot provide
  authority user/session ids.
- `AIOrchestrator.observe` is the only Orchestrator entry used. Legacy execute
  and EventBus publish remain blocked from the assembly path.
- Advisory Skill projection is injected and remains SELECT-only.
- `event_name` and `payload` are ephemeral and are not persisted or copied into
  the immutable envelope.
- Role, permissions, scope and session elevation are not copied.
- The assembly module has no direct SQLAlchemy Session dependency.
- There is no Work creation, Skill execution, Approval/Memory mutation,
  production route wiring, public API change or schema migration.
- Future mutating consumers must reload current user/session state and
  reauthorize current scope and the exact Skill, failing closed on stale,
  revoked, expired, disabled or unauthorized authority.

Residual risk remains at the production bridge boundary. Authenticated capture,
durable advisory proposal identity/idempotency, crash recovery, Work
materialization, Approval semantics and governed dispatch still require
separate designs and gates before advisory decisions may produce actions.

### 25J — Durable authenticated advisory proposals

The system may persist an immutable authenticated advisory proposal for crash
recovery and idempotent advisory continuity. Persistence itself remains
non-executable and grants no authority.

Controls: identity is scoped by authenticated user + authentication session +
canonical idempotency key; the snapshot is protocol-bound and digest-verified;
payload/event name and decision internals are excluded; database bounds and a
unique constraint fail closed; transaction races re-read the exact identity
after rollback.

Residual risk remains intentionally outside 25J: proposal consumption must
reload and reauthorize current authority. Work materialization, Approval
bridging, governed Skill execution, EventBus integration, and public route
capture require separate architecture designs and gates.

### 25K — Authenticated advisory proposal reauthorization validation

25K reduces the risk that durable advisory metadata is mistaken for live
authority. A stored proposal can influence no action until one exact binding
candidate is checked against the current authenticated user/session, current
catalog state, and current Skill/scope policy.

Controls: exact proposal user/session ownership is required; current session
revocation, expiry, token identity and user activity are rechecked; binding,
version and Skill drift fails stale; `authorize_skill_execution` re-applies
current role, current server-derived elevation, capability rules, account scope,
and user scope against the ephemeral candidate input.

The 25K result is a frozen ephemeral validation, not an authority token, and
does not survive TOCTOU. There is no runtime invocation, no Work or Approval
mutation, no EventBus integration, no database write/lock, and no public route
or schema/OpenAPI change.

Residual risk remains intentionally deferred to a later dispatch/materialization
boundary: final execution must reauthorize again, Approval semantics remain
separate, and binding configuration cannot become execution authority under
`authenticated_advisory_v1`.
