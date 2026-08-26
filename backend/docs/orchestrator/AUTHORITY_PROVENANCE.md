# Authenticated Authority Provenance Reference

Commit 25G establishes an internal, immutable reference to the user and auth
session that were already authenticated by the server. The reference is
provenance only. It grants no authority, is not an authorization decision and
is not executable intent.

## Allowed reference

`AuthorityProvenance` contains only:

- `authority_user_id`;
- `auth_session_id`;
- optional bounded `request_id` correlation metadata;
- fixed source `authenticated_http_session`.

`authority_user_id` and `auth_session_id` are derived only from the existing
`AuthenticatedSession`. The factory also requires the auth session's `user_id`
to match the authenticated user's id.

## Explicit exclusions

The reference does not copy role, permissions, account scope, subject scope,
session elevation, Approval state, Skill or binding selection, input payload,
runtime context, Memory, credentials, passwords, cookies or tokens.

Those omissions are security boundaries. Persisting a role, permission set or
elevation decision inside provenance would turn historical metadata into stale
authorization state.

## Future consumption rule

A future consumer of this reference must reload the current user, reload the
current auth session, validate that the session is still active, recalculate
current role and permissions, reauthorize current scope and reauthorize the
exact Skill before any action can be proposed or executed.

A missing, expired or revoked session, a missing or disabled user, scope loss or
Skill authorization loss must fail closed. The provenance object itself never
authorizes an action.

## 25G first APPLY boundary

There is no production EventBus wiring in the first 25G APPLY. The new module
has no database access and does not import the legacy Orchestrator, advisory
Skill projection, Work services, governed Skill execution, Approval, Skill
runtime or Memory mutation services.

The first APPLY creates no Work, executes no Skill, mutates no Approval or
Memory, exposes no public API and introduces no database schema or Alembic
migration.

## Deferred bridge work

Capturing provenance from a live authenticated route, passing it beside an
OrchestrationDecision, persisting proposal identity, materializing Work,
dispatching a governed Skill and integrating Approval semantics remain separate
future checkpoints.
