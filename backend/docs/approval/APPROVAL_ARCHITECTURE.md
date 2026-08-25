# Approval Architecture V1

**Status:** Extended through Commit 24D

## 1. Purpose

The approval layer answers three independent questions:

1. What exact action is being proposed?
2. Does that action require a human decision?
3. Which human authority is allowed to decide it?

24A implements only the durable answer to questions 1 and 3 for Skill
execution requests. Autonomous policy selection and governed execution arrive
later in Commit 24.

## 2. Trust boundary

```text
Requester (user/agent/system/integration)
        |
        v
ApprovalService
        |
        +-- exact published SkillVersion
        +-- capability consistency
        +-- canonical input digest/fingerprint
        +-- safe target IDs
        +-- deterministic risk
        |
        v
ApprovalRequest [pending]
        |
        v
Human decision with server-side RBAC
        |
        v
ApprovalDecision [approved/rejected]
```

There is intentionally no arrow from 24A to `SkillRuntimeService.invoke()`.

## 3. Actor boundary

Requester actor types match the existing internal runtime attribution model:
`user`, `agent`, `system`, and `integration`.

For a user requester, the canonical reference is `user:<id>` and the user must
be active when the request is created. Non-human requesters cannot carry a user
ID.

This is audit attribution for a proposal. It is not execution authority.

## 4. Exact action binding

The request stores:

- exact `skill_version_id`;
- published `manifest_digest` inside the request fingerprint;
- SHA-256 digest of canonical input;
- requester identity;
- bounded idempotency identity;
- safe `target_account_id` / `target_user_id` metadata when the Skill declares
  those scopes.

Raw input is never stored by the approval domain.

## 5. Risk and authority

24A classification is deliberately deterministic:

| Executable state | Risk | Required decision permission |
| --- | --- | --- |
| `read_only` with read-only capabilities | low | `approval:decide` |
| `mutating` without external capability | high | `approval:decide` |
| `external` | critical | `approval:decide_sensitive` |

A capability with `resource_scope=external` is valid only under
`execution_mode=external`. Inconsistent published state is rejected.

## 6. Separation of duties

For `high` and `critical` requests originating from a user, that same user
cannot be the human decider.

The requester and decider therefore remain independent identities. Later
Commit 24 stages may introduce stronger organization-specific quorum policy,
but must not weaken this baseline.

## 7. Explicit exclusions

24A contains:

- no Approval API route;
- no automatic approval;
- no autonomous Skill selection;
- no Skill execution;
- no `SkillInvocation` creation;
- no Work or Memory mutation;
- no interpretation of model output as authority;
- no permission grant from `AgentSkillBinding`;
- no bypass of existing Skill RBAC/scope checks.

24B will expose human approval operations. Later stages will introduce
autonomy policy and governed execution.

## 8. 24B API and authority boundary

```text
Authenticated human
        |
        +-- propose exact Skill action
        |      |
        |      +-- existing Skill RBAC/scope authorization
        |      +-- server-derived user requester
        |      +-- mandatory Idempotency-Key
        |
        +-- approval queue/read
        |      |
        |      +-- approval:read
        |      +-- sensitive rows hidden without
        |          approval:decide_sensitive
        |
        +-- approve/reject
               |
               +-- request.required_permission
               +-- separation of duties in ApprovalService
               +-- elevated session for critical
```

The Approval API exposes managerial approval metadata only. It never exposes
raw Skill input, the stored digest/fingerprint, the idempotency key or the
requester's durable internal reference.

Public request creation is intentionally restricted to a human who is already
authorized by the existing Skill execution boundary for the exact version and
scope. This does not make approval redundant: 24B establishes the human
workflow boundary while later checkpoints decide when an autonomous requester
must enter that workflow.

There remains no path from the Approval router to `SkillRuntimeService`.

## 9. 24C deterministic autonomy boundary

`evaluate_skill_autonomy` consumes only actor type plus exact published Skill
executable state. Low-risk non-human actions are eligible for autonomous
execution; high/critical actions require human approval; users cannot be
impersonated through the autonomous path.

The policy does not create Approval rows, consume decisions or invoke runtime.
`classify_skill_risk` is shared with `ApprovalService` to prevent risk drift.
24D must still establish non-human authority, re-authorize current Skill/scope
and satisfy required approval before runtime.

## 10. 24D governed execution

```text
server-resolved non-human actor + current authority user
        |
        +-- exact current Skill/capabilities
        +-- 24C autonomy policy
        +-- existing Skill RBAC/scope authorization
        |
        +-- low read_only ----------------------> runtime
        |
        +-- high/critical
               |
               +-- approved + unexpired exact request
               +-- current human decision authority
               +-- sensitive elevation evidence when critical
               +-- one-time approval consumption reservation
               +-- deterministic runtime idempotency
               |
               v
             runtime
```

The Approval record never replaces current RBAC/scope authorization. No public
24D route exists; 24E owns Work/Orchestrator integration.
