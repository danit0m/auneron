# Authenticated Advisory Context Envelope

Commit 25H establishes an internal immutable envelope that composes the
advisory decision context already produced by 25F with the authenticated
authority provenance introduced by 25G.

The envelope grants no authority, is not an authorization decision and is not
executable intent.

## Envelope fields

`AuthenticatedAdvisoryEnvelope` contains only:

- `decision` — the existing immutable `OrchestrationDecision`;
- `plan` — the existing immutable `AdvisorySkillBindingPlan`;
- `authority` — the existing immutable `AuthorityProvenance`.

No payload, runtime context, Work identity, Approval identity, role, permission
set, scope, session elevation, credentials, tokens or Memory state is copied
into the envelope.

## Integrity invariants

`plan.decision_name` must equal `decision.decision_name`.

The `agent_name` sequence in `plan.agents` must exactly equal
`decision.selected_agents`, preserving both order and membership. An empty
selected-agent set remains an empty plan.

These checks prove structural consistency only. They do not authorize any
action.

## Future authority rule

Any future consumer must reload the current user, reload the current auth
session, validate that the session remains active, recalculate current role and
permissions, reauthorize current scope and reauthorize the exact Skill before
an advisory envelope can influence Work or governed execution.

Missing, expired, revoked or disabled authority, scope loss or Skill
authorization loss must fail closed.

## 25H first APPLY boundary

There is no production EventBus wiring and no production route wiring in the
first 25H APPLY. The envelope module has no database access and does not create
Work, execute Skills, mutate Approval or Memory, expose a public API or
introduce a database schema or Alembic migration.

## Deferred bridge work

Live authenticated capture, EventBus/envelope wiring, durable proposal
persistence, proposal idempotency identity, Work materialization, Approval
integration and governed Skill dispatch remain separate future checkpoints.
