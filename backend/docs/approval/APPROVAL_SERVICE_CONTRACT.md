# Approval Service Contract V1

**Status:** Extended through Commit 24D

## 1. Transaction ownership

`ApprovalRepository` performs SQLAlchemy statements and `flush` only. It never
calls `commit`, `rollback`, `begin` or `begin_nested`.

`ApprovalService` owns request and decision transactions.

## 2. Request creation

`create_skill_execution_request`:

1. normalizes requester and mandatory idempotency identity;
2. canonicalizes JSON input with size/depth bounds;
3. requires an active Skill and exact published version;
4. reads every capability declaration;
5. rejects inconsistent execution-mode/capability state;
6. resolves only safe account/user target IDs;
7. calculates input digest and exact request fingerprint;
8. classifies risk and required decision permission;
9. persists one pending request;
10. commits exactly through the service boundary.

The service never persists raw input.

Equivalent retry with the same requester/key/fingerprint returns the existing
request. Reusing the key for different canonical input raises an idempotency
conflict.

## 3. Human decision

`decide` requires an active human `User`.

The request is row-locked before decision. The server checks the role snapshot
against the request's stored required permission.

`high` and `critical` user-originated requests enforce separation of duties:
the requester cannot decide the same request.

Expired requests become terminal `expired` without a decision row. Approved
and rejected requests are terminal and immutable.

## 4. No execution side effect

24A must never:

- import or resolve a handler;
- call `SkillRuntimeService.invoke`;
- insert a `SkillInvocation`;
- mutate Work or Memory;
- treat binding/configuration/model content as authority.

An `approved` status means only that a human decision exists. Future governed
execution must independently re-authorize the exact action and current scopes.

## 5. Sensitive decisions

`critical` Skill requests require `approval:decide_sensitive`.

24A has no HTTP session context. Therefore recent-authentication/elevated-
session enforcement belongs to the 24B API boundary and must be applied there
in addition to this service permission check.

## 6. 24B read/list contract

`ApprovalRepository.list_requests` remains transaction-free and supports only
bounded filters required by the API: lifecycle status, risk level, required
decision permission and monotonic `after_id`.

`ApprovalService.list_requests` validates those filters and the bounded page
size before delegating to the repository. It contains no HTTP/session logic.

The API derives visible required permissions from the authenticated role. A
manager therefore cannot enumerate or read a request whose required permission
is `approval:decide_sensitive`.

## 7. 24B HTTP decision contract

The router never accepts requester or decider identity from JSON. Requester and
decider user IDs are derived from the authenticated session.

Before public request creation, the existing Skill authorization boundary is
re-evaluated against the exact input and current session, including elevated
session requirements for external execution.

Before decision, the API checks visibility and dynamic approval permission.
A `critical` request additionally requires a currently elevated session. The
service then row-locks and re-checks durable permission/separation-of-duties
rules before persisting the terminal decision.

No Approval HTTP operation calls the Skill runtime.

## 8. 24C shared classification contract

`ApprovalService` and the autonomy policy share `classify_skill_risk`.
The policy is side-effect free and returns only `autonomous_allowed`,
`approval_required` or `blocked`. None of those results executes a Skill in
24C.

## 9. 24D consumption contract

`ApprovalRepository` remains transaction-free. `GovernedSkillExecutionService`
owns the reservation/finalization transactions used by approval consumption.

The service re-computes the exact input digest and Approval request fingerprint,
revalidates current policy, current human approval authority and current Skill
RBAC/scope before entering runtime.

Critical Approval decisions require `sensitive_elevation_verified=True`.
The HTTP decision route derives this flag from the authenticated elevated
session; direct service calls cannot silently manufacture a critical decision
without explicitly satisfying the same contract.
