# Governed Work Skill Execution — 24E.2

**Status:** implementation checkpoint for Commit 24; internal boundary only.

## 1. Purpose

24E.2 connects one durable `WorkItem` to one exact published Skill action without
turning Work content into execution authority. The integration remains internal:
there is no public autonomous Skill route and the legacy Orchestrator pipeline
does not receive dispatch authority in this checkpoint.

## 2. Authority boundary

Authority is server-side and independent from Work data:

- actor type is always `system`;
- actor reference is derived as `system:work:<work_item_id>`;
- the authority user ID is persisted as an internal reference and the current
  active user/RBAC/scope is revalidated before every new dispatch;
- `context_data`, title, description, origin fields, Memory, agent bindings,
  model output and client-provided values never grant execution authority;
- the existing `GovernedSkillExecutionService` still owns autonomy policy,
  Approval validation/consumption and current Skill authorization.

Work scope is only a consistency constraint. The currently authorized Skill
scope must equal the Work scope before configuration or dispatch.

## 3. Durable ledger

`work_skill_executions` is a one-to-one ledger with `work_items`.

It stores:

- exact `skill_version_id`;
- exact input digest, but **not raw input**;
- server-derived actor and dispatch key;
- authority user reference plus role snapshot for audit;
- optional ApprovalRequest / ApprovalConsumption links;
- optional SkillInvocation link;
- dispatch attempt count and terminal status.

Raw input must be re-supplied by a trusted internal caller. Its canonical digest
must equal the configured digest. This allows model/planner output to be action
data without making it an authority source and avoids creating a second raw
payload store.

Recurring work is already represented by a newly generated WorkItem, so V1 uses
one exact Skill action per WorkItem instead of an attempt collection hidden in
`context_data`.

## 4. Work lifecycle mapping

| Work Skill state | WorkItem state |
| --- | --- |
| configured/read-only ready | `ready` |
| mutating awaiting human Approval | `ready` |
| actual dispatch begins | `in_progress` |
| SkillInvocation succeeds | `completed` |
| SkillInvocation fails | `blocked` |
| isolated runtime times out | `blocked` |
| Approval is rejected/cancelled/invalid after decision | `cancelled` |

The normal Work transition API remains the only component that changes Work
status. That preserves dependency checks, optimistic versioning and WorkEvent
idempotency. No execution-specific WorkEvent vocabulary is added in 24E.2;
`status_changed` remains the Work audit event while the new relational ledger
contains Skill/Approval linkage.

## 5. Idempotency and recovery

- read-only runtime key: `work:<work_id>:skill:<version_id>`;
- mutating Approval key: `work:<work_id>:approval`;
- mutating runtime key remains `approval:<approval_request_id>`;
- Work status event keys are derived server-side.

Before a retry submits a handler, the service searches for the deterministic
SkillInvocation. Existing `running` or terminal invocations are reconciled and
are never submitted again.

If the application crashes after Work enters `in_progress` but before the
runtime creates a SkillInvocation, reconciliation returns `retry_required`.
A trusted internal caller must re-supply the exact input; its digest must match
the ledger before a new governed dispatch can occur.

The existing runtime guarantee remains important: a SkillInvocation `running`
row is committed before a handler is submitted. Therefore an absent invocation
means there is no durable evidence that the handler was submitted. This still
does **not** turn external effects into exactly-once semantics.

## 6. Approval behavior

`read_only` Work does not create Approval.

`mutating` Work creates or reuses one exact ApprovalRequest with the Work
system actor and stays `ready` while the request is pending. Approved requests
are handed to `GovernedSkillExecutionService`, which rechecks exact action,
current authority, scope, expiry, human decision and one-time consumption.

`external` Work dispatch remains blocked in 24E.2 even though the lower
governed service understands critical Approval. External-effect reconciliation
is a later hardening boundary.

## 7. Abuse and observability

Before a **new** handler submission, the existing Skill rate limiter consumes
against `authority_user_id`. Replay of an existing SkillInvocation does not
consume another dispatch budget.

The limiter remains in-process and is not a distributed production control.

Structured logs contain only safe identifiers/status metadata. They do not log
raw input, actor reference, dispatch key, Approval fingerprint, idempotency key
or raw exception details.

## 8. Orchestrator boundary

The legacy Orchestrator remains observational/decision-oriented in 24E.2.
Neither its routes nor agent pipeline gain access to:

- `WorkSkillExecutionService`;
- `GovernedSkillExecutionService`;
- `WorkRepository`;
- `ApprovalRepository`;
- `SkillRepository`.

A later controlled adapter may request Work dispatch only after its
server-authority contract, recovery loop and operational controls receive their
own Gate.

## 9. Explicit non-claims

24E.2 does not claim:

- public autonomous execution;
- untrusted/plugin autonomy;
- filesystem sandboxing;
- distributed rate limiting;
- exactly-once external side effects;
- automatic recovery when exact input is unavailable;
- production readiness.

## 24E.3 recovery, observability and Orchestrator decision

24E.3 adds a separate non-executing Work Skill maintenance layer. Skill
stale-running recovery executes first; Work Skill recovery then calls only
`WorkSkillExecutionService.reconcile()` against bounded durable candidates.
The maintenance path never receives raw input and never calls `dispatch()`.

Governed Work dispatch now has a dedicated authority-user rate limiter so
autonomous submissions do not share the explicit human Skill API bucket.
Both controls remain in-process defense-in-depth; distributed enforcement is
still a Production Readiness requirement.

Work Skill observability uses a strict safe-field allowlist and excludes raw
input/output, digests, dispatch/idempotency keys, actor references, credentials
and raw exception text.

The legacy Orchestrator is deliberately unchanged in 24E.3. Decisions, model
context, registry selection and agent output remain context/intent, not
authority. A controlled adapter is deferred until after the Commit 24
cumulative gate.

`external` Work dispatch remains blocked because database idempotency alone
does not establish exactly-once semantics for external business side effects.

## 25D authorized learning runtime context

25D adds an optional internal side-band learning context only for
`internal_python` `read_only` Work execution. A SkillVersion must declare
`runtime_context_protocol=work_learning_v1`, and the trusted handler registry
must independently opt in to the same protocol.

The Work execution resolves or reuses one durable immutable context snapshot
after current Skill authorization and rate limiting but before Work starts and
before `dispatch_attempts` is incremented. Snapshot access independently
revalidates current Work-read and Memory-read authority.

A creating snapshot commit is followed by reacquisition of the Work/Execution
locks and current Skill authorization before dispatch continues. Context
failure therefore cannot start Work or run the handler. No contextless fallback
is allowed for an opted-in execution.

`input_payload`, Work input digest, Approval identity, dispatch key and the
mutating Approval path are unchanged. Mutating and external runtime context
remain forbidden. See `WORK_LEARNING_RUNTIME_CONTEXT.md`.
