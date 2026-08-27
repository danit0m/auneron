# Authenticated Advisory Envelope Assembly Service

Commit 25I introduces one internal composition service for the already-approved
authenticated advisory envelope. It does not expose a route and does not grant
execution authority.

## Assembly contract

`AuthenticatedAdvisoryEnvelopeAssemblyService.assemble(...)` accepts:

- an existing server-authenticated `AuthenticatedSession`;
- a bounded non-blank `event_name`;
- an ephemeral `payload` dictionary;
- an optional request correlation id.

The authority source is authenticated session only. The caller cannot provide
`authority_user_id` or `auth_session_id`.

The assembly sequence is fixed:

1. derive `AuthorityProvenance` from the authenticated session;
2. call `AIOrchestrator.observe(event_name, payload)`;
3. call the injected `OrchestratorSkillBindingProjectionService.resolve(...)`
   with the exact returned decision;
4. construct and return `AuthenticatedAdvisoryEnvelope`.

## Non-execution boundary

There is no EventBus publish and no call to legacy `AIOrchestrator.execute`.
There is no Work creation, no Skill execution and no Approval or Memory
mutation.

The projection dependency remains SELECT-only and injected. This assembly module
has no direct SQLAlchemy `Session` or database dependency.

## Ephemeral input boundary

`event_name` and `payload` are assembly inputs only. They are not copied into
the immutable envelope and there is no persistence of either value.

Role, permissions, scope and session elevation are also not copied into the
envelope.

## Production wiring boundary

The first 25I APPLY has no production route wiring and no authenticated EventBus
integration. It adds no public API, database schema or Alembic migration.

## Future authority rule

The envelope remains provenance only. Before any future mutating consumer may
create Work, dispatch a Skill or consume an Approval, it must reload the current
user, reload the current auth session, validate current authority, reauthorize
current scope and reauthorize the exact Skill. Stale, revoked, expired, disabled
or unauthorized authority must fail closed.

## Deferred work

Production route capture, authenticated EventBus integration, durable advisory
proposal persistence, proposal idempotency identity, Work materialization,
Approval bridge and governed Skill dispatch remain separate checkpoints.
