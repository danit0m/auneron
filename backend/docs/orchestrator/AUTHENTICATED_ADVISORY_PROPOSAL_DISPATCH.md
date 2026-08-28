# Authenticated advisory proposal governed dispatch (25L)

25L is the first internal action bridge from one durable authenticated advisory
proposal candidate into the existing governed Skill execution boundary. It is
deliberately narrow: only a candidate revalidated as `read_only` +
`internal_python` is eligible.

The adapter does not accept an earlier 25K validation result from its caller.
Instead it normalizes the exact ephemeral `input_payload` with
`approval_input_identity`, then calls
`AuthenticatedAdvisoryProposalConsumptionService.validate` inside the same
dispatch request. That 25K value remains ephemeral evidence only; it is not an
authority token and does not survive TOCTOU.

For an eligible candidate, 25L derives all autonomous execution identity on the
server:

- actor type is `agent`;
- actor reference is `agent:<validated agent_name>`;
- actor user id is `None`;
- runtime idempotency key is
  `advisory:<proposal_id>:<binding_id>`;
- `authority_user_id` is the current authority id returned by 25K;
- `approval_request_id` is `None`;
- `runtime_context` is `None`.

The final action is delegated only to
`GovernedSkillExecutionService.execute`. 25L never calls
`SkillRuntimeService.invoke` directly. Governed execution reloads the current
authority and current Skill/version/capabilities, evaluates the existing
autonomy policy, requires a trusted isolated autonomy handler, and calls
`authorize_skill_execution` again immediately before runtime.

The derived runtime idempotency key intentionally identifies one proposal
binding candidate rather than one arbitrary payload. A retry with the same
candidate and the same canonical input replays the existing SkillInvocation
without a second handler execution. Reusing the same proposal/binding candidate
with a different canonical input changes the existing request fingerprint and
fails closed with the runtime idempotency conflict instead of executing a second
action.

25L does not enable `mutating`, `external`, or autonomous `plugin` dispatch.
Those cases remain deferred to separate Approval-sensitive architecture
boundaries. The adapter creates no Work, creates/decides/consumes no Approval,
publishes no EventBus event, mutates no Memory, and has no public route,
production wiring, schema migration, Alembic change, or OpenAPI change.

The dispatch result is frozen and exposes only proposal/binding/Skill identity,
server-derived actor reference, invocation identity/status, duplicate state, and
the governed Skill output. It contains no role, permissions, authentication
session, token, runtime idempotency key, raw input, Approval state, or Work
state.
