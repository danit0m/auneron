# Authenticated advisory mutating Approval bridge (25M)

25M introduces one internal, non-routed bridge from an authenticated advisory
candidate to the existing Approval and governed Skill execution boundaries.
It does not make the proposal, the 25K validation result, the ApprovalRequest,
or the caller an authority token.

## Frozen eligibility

The bridge accepts `mutating + internal_python ONLY`.

`read_only` remains owned by 25L. The bridge performs no external execution
and autonomous `plugin` execution remains blocked.

Both `request_approval(...)` and `dispatch_approved(...)` normalize the input
with `approval_input_identity` and then call
`AuthenticatedAdvisoryProposalConsumptionService.validate`. A validation
result is ephemeral and is never accepted from the caller or reused as
authorization between phases.

## Approval request phase

The requester is server-derived as `agent:<agent_name>` with actor type
`agent` and no requester user id. The stable Approval identity is also
server-derived:

`advisory:<proposal_id>:<binding_id>`

`ApprovalService.create_skill_execution_request` receives the exact currently
revalidated SkillVersion and the same canonical input. For an eligible 25M
candidate the persisted ApprovalRequest must correlate exactly with the
server-derived actor, key, SkillVersion, canonical input digest, high risk,
`approval:decide`, and current account/user scope.

A same-candidate same-input retry replays the same ApprovalRequest. Reusing
that candidate identity with different canonical input conflicts and cannot
create a second ApprovalRequest. Rejected, expired, or cancelled Approval
state is terminal for that candidate identity; a new advisory proposal is
required.

25M never decides Approval. The human decision boundary remains the existing
authenticated /approvals API, including its own current authority and
elevation checks.

## Approved dispatch phase

`dispatch_approved(...)` accepts the opaque `approval_request_id`, but it
re-runs `AuthenticatedAdvisoryProposalConsumptionService.validate` against the
same canonical input before any execution. It reloads the ApprovalRequest and
requires exact candidate correlation again.

The final action is only `GovernedSkillExecutionService.execute` with the
current revalidated SkillVersion, server-derived agent actor, current
authority user id, canonical input, `idempotency_key=None`, the exact
`approval_request_id`, and `runtime_context=None`.

The governed boundary remains responsible for the final lock and revalidation
of the approved/unexpired request, exact actor/version/input fingerprint,
current human decider permission, current Skill RBAC/scope authorization,
trusted internal handler, one-time ApprovalConsumption reservation, and the
runtime ledger. Runtime idempotency is `approval:<approval_request_id>` and is
owned by governed execution.

A same approved dispatch retry resolves the same invocation without a second
handler action. Different input cannot consume the Approval for another
action.

## Explicit non-goals

25M has no direct SkillRuntimeService invocation, no Approval decision
mutation, no external execution, no Work materialization, no Memory
integration, no EventBus integration, no public route, no production wiring,
no schema change, no Alembic change, and no OpenAPI change.

External/sensitive advisory execution, Decision-to-Work materialization,
production EventBus wiring, public advisory action routes, and Memory feedback
remain separate future checkpoints.
