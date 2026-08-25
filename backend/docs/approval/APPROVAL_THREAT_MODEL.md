# Approval Threat Model V1

**Status:** Extended through Commit 24D

## 1. Protected assets

- exact identity of the proposed action;
- independence between requester and human decider;
- immutable terminal decision history;
- existing Skill RBAC and scope boundaries;
- raw business input and credentials;
- audit continuity across user lifecycle.

## 2. Threats and controls

| Threat | 24A control |
| --- | --- |
| model/binding grants itself authority | non-authority rule; separate Approval tables/service |
| requester changes payload after approval request | canonical input SHA-256 + request fingerprint |
| idempotency key reused for a different action | requester-scoped unique key + fingerprint conflict |
| non-human actor impersonates user | actor/user consistency and canonical user reference |
| untrusted raw payload retained in approval history | only digest and safe target IDs persisted |
| mutating user approves own sensitive action | high/critical separation of duties |
| manager approves external/critical operation | `approval:decide_sensitive` not granted to manager |
| duplicate/concurrent human decisions | row lock + unique decision FK + terminal lifecycle |
| deleted historical user erases audit | `SET NULL` user FK plus reference/role/permission snapshots |
| approval bypasses Skill authorization | architecture explicitly requires future re-authorization |
| stale approval remains actionable forever | bounded `expires_at`; expired becomes terminal |
| decision note stores secrets | bounded optional note and explicit operational prohibition |

## 3. Non-authority rule

None of the following is a grant:

- Skill manifest or capability;
- `AgentSkillBinding`;
- Work item, comment, dependency or linked Memory;
- model plan, tool call, confidence or explanation;
- external/plugin content;
- Approval risk classification by itself.

Only server-side human authority can create a terminal approval decision.
Even an approved decision does not replace current RBAC or scope authorization.

## 4. Deferred to later Commit 24 stages

- public Approval API and anti-IDOR reads;
- elevated session for sensitive human decisions;
- autonomy policy determining whether a request is needed;
- governed non-human Skill invocation;
- one-time/consumable approval semantics at execution;
- Work/Orchestrator integration;
- approval observability, maintenance and final cumulative security gate.

## 5. Additional 24B controls

| Threat | 24B control |
| --- | --- |
| caller spoofs requester/decider identity | public schemas forbid actor fields; identity is session-derived |
| caller proposes inaccessible Skill/scope | existing Skill authorization runs before ApprovalService request creation |
| manager enumerates critical approvals | list/read visibility is filtered by stored required permission |
| stale credential approves critical action | `critical` decisions require a currently elevated session |
| malformed/oversized Approval request | dedicated 128 KiB HTTP cap plus strict Pydantic schemas and 64 KiB canonical input bound |
| Approval error leaks internals | dedicated sanitized error envelope with request ID and `no-store` |
| raw input/idempotency/digest leaks via response/log | public schemas omit them and Approval observability uses an explicit safe-field allowlist |
| database outage leaks SQL/credentials | Approval middleware converts `OperationalError` to sanitized 503 |
| API approval executes the action | no Approval route imports or calls `SkillRuntimeService` |
| approval replaces runtime authorization | later execution must still re-authorize current RBAC/scope and exact action |

## 6. Additional 24C controls

| Threat | 24C control |
| --- | --- |
| context grants itself autonomy | policy accepts only server-side actor type plus exact Skill executable state |
| Approval/autonomy classify risk differently | one shared `classify_skill_risk` |
| mutating action runs autonomously | every high-risk non-human decision requires approval |
| external action runs without sensitive approval | critical requires `approval:decide_sensitive` |
| autonomous path impersonates a user | `user` is blocked from the autonomous path |
| invalid capability/mode is downgraded | invalid state raises error, never allow |
| policy result executes code | 24C policy has no runtime or invocation dependency |

## 7. Still deferred after 24C

- governed non-human Skill invocation;
- exact approval satisfaction/consumption at execution;
- current RBAC/scope re-authorization for non-human execution;
- Work/Orchestrator integration;
- approval maintenance, distributed abuse controls and final cumulative gate.

## 8. Additional 24D controls

| Threat | 24D control |
| --- | --- |
| same approval executes twice | unique approval consumption + deterministic runtime idempotency |
| crash between approval and runtime | committed reservation survives and retries resolve the same runtime identity |
| approval used for changed input/version/actor | exact digest/fingerprint/version/actor comparison |
| old approval bypasses current RBAC | current authority user and current scope are re-authorized before runtime |
| critical decision loses elevation provenance | persisted `sensitive_elevation_verified` |
| stale approved request executes later | governed execution rejects `expires_at <= now` |
| model/Work/binding selects its own authority | 24D is internal and requires a server-resolved authority principal |
| runtime terminal failure leaves approval reusable | any persisted invocation finalizes the approval as consumed |
| pre-ledger runtime failure enables retry with same approval | consumption becomes terminal `failed` |

24E remains responsible for Work/Orchestrator integration, stale reservation
maintenance, abuse controls and the final cumulative operational gate.

## 24D autonomy handler containment rule

The governed execution boundary treats handler trust as a separate server-side
control. An allowlisted handler is not automatically autonomous. 24D permits
autonomous execution only for exact `internal_python` registrations marked
`trusted_for_autonomy=True`; plugin/untrusted handlers remain blocked until a
stronger process/container isolation boundary exists. Thread timeout is not a
hard kill and is not represented as one.
