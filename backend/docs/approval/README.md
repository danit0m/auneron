# Approval & Autonomy

**Roadmap:** Commit 24

**Status:** 24D Governed Skill Execution

Approval & Autonomy is the authority layer that separates a proposed action
from permission to execute it.

Commit 24A introduces only the durable approval domain:

- an exact `ApprovalRequest` bound to one published Skill version and canonical
  input digest;
- deterministic risk classification;
- explicit required human approval permission;
- one terminal `ApprovalDecision`;
- separation of duties for sensitive user-requested actions;
- transaction-owned service and transaction-free repository;
- database constraints and retention rules.

24A does **not** expose an Approval HTTP API, execute a Skill, create a
`SkillInvocation`, select Skills autonomously or allow a client to choose a
non-human execution actor.

## Documents

- `APPROVAL_ARCHITECTURE.md`: trust and component boundaries.
- `APPROVAL_DATA_MODEL.md`: tables, constraints and retention.
- `APPROVAL_SERVICE_CONTRACT.md`: request/decision transaction contract.
- `APPROVAL_THREAT_MODEL.md`: abuse cases and 24A controls.

## Authority rule

The following remain context or discovery data, never authority:

- `AgentSkillBinding`;
- Skill manifests and capabilities;
- Work items, comments or linked Memory;
- model-generated plans, tool calls or confidence;
- plugin or external-system text.

An approval record is also not a substitute for RBAC or resource-scope
authorization. Future execution must re-check current authority and resource
state before calling the Skill runtime.

## 24B HTTP boundary

24B exposes exactly four human-facing operations under `/approvals`:

- create an exact Skill-execution approval request;
- list requests visible to the authenticated approval role;
- read one visible request and its terminal decision;
- approve or reject one request.

All Approval routes require the base API key plus an authenticated session.
Creation additionally requires current Skill execution authority for the exact
version and scope. Sensitive (`critical`) requests are hidden from roles that
lack `approval:decide_sensitive`, and sensitive decisions also require a
currently elevated session.

The API never accepts requester/decider actor identity from JSON. It does not
expose the idempotency key, request fingerprint, input digest, required
permission or raw input.

24B still does **not** execute a Skill or create `SkillInvocation`. An approved
record remains a human authorization artifact only; governed execution and
approval consumption arrive in later Commit 24 checkpoints.

## 24C autonomy policy boundary

For non-human actors, valid low-risk `read_only` actions are
`autonomous_allowed`. `mutating` and `external` actions are
`approval_required`; external actions require
`approval:decide_sensitive`. Human `user` actors are blocked only from the
autonomous path. 24C adds no execution side effect.

## 24D governed execution boundary

24D adds an internal, non-HTTP execution service. Low-risk non-human read-only
actions may execute only after current Skill RBAC/scope authorization. High and
critical actions require an exact approved, unexpired ApprovalRequest plus
current Skill authorization.

Approval use is one-time and durable through `approval_consumptions`. The
reservation is persisted before runtime and is linked to the deterministic
SkillInvocation identity after execution/replay. Work/Orchestrator integration
remains deferred to 24E.

## 24D handler trust guardrail

Governed non-human execution is fail-closed at the handler boundary: only exact
`internal_python` handlers explicitly registered with
`trusted_for_autonomy=True` can enter the autonomous runtime path. Explicit
human Skill invocation remains governed by the existing Skill API and does not
inherit autonomous trust from this flag.
