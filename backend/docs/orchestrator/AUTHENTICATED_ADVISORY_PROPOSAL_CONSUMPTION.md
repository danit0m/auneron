# Authenticated Advisory Proposal Consumption

## Purpose

25K introduces `AuthenticatedAdvisoryProposalConsumptionService` as an
internal, non-routed, SELECT-only validation boundary for one exact persisted
binding candidate.

The stored proposal grants no authority. It is durable advisory provenance
only. A successful 25K result is also not an authority token and is not
reusable authorization.

## Current authority reload

The caller supplies the server-derived `AuthenticatedSession`; user id,
authentication-session id, role, permissions, and elevation are not accepted as
independent authority inputs.

For every validation the service reloads the current reloaded User and
AuthSession from the database. The durable proposal must belong to that exact
user and authentication session. The current session must still exist, belong
to the same user, retain the same token identity, remain unrevoked and
unexpired, and the current user must remain active.

Role is read from the reloaded User. Session elevation is derived from the
reloaded AuthSession at validation time.

## Persisted snapshot integrity

The service revalidates protocol `authenticated_advisory_v1`, canonical
snapshot digest, stored byte/count metadata, ordered selected agents, ordered
agent entries, and the safe binding allowlist.

The requested `binding_id` must occur exactly once. Missing binding membership
is opaque not-found; malformed, duplicate, corrupt, or inconsistent persisted
snapshot state fails stale.

## Exact current Skill binding

25K reloads the exact current AgentSkillBinding, SkillVersion, and
SkillDefinition referenced by the persisted candidate. The binding must remain
enabled and preserve the persisted agent name, version id, and priority. The
version must remain published and preserve skill id, execution mode, and runtime
kind. The Skill must remain active.

Binding configuration is deliberately not part of
`authenticated_advisory_v1`. It is not execution authority. If future runtime
behavior needs persisted binding configuration, the proposal protocol must be
versioned before old proposals may rely on it.

## Current scope reauthorization

After snapshot and catalog revalidation, 25K calls
`authorize_skill_execution` for the exact persisted Skill version. It uses the
current reloaded role, the current user id, current server-derived session
elevation, and the exact ephemeral `input_payload` intended for this candidate.

This re-applies current `skill:execute` RBAC, mutating/external permissions,
external elevation, capability/resource checks, account scope, and user scope.
Capabilities remain declarations, never authority.

The input_payload is ephemeral. 25K does not persist or log it.

## Result and TOCTOU boundary

`AuthenticatedAdvisoryProposalConsumptionValidation` is a frozen, ephemeral
allowlist containing proposal/binding identity and the scope ids returned by
current Skill authorization. It excludes role, permissions, capability
objects, raw payload, credentials, tokens, and execution intent.

The validation does not survive TOCTOU. Any later mutation, dispatch, Approval
consumption, Work materialization, or Skill execution must reauthorize again at
its final governed execution boundary.

## Explicit non-goals

25K is SELECT-only and performs no runtime invocation, no Work or Approval
mutation, no Memory mutation, no EventBus publish, no legacy Orchestrator
execution, no INSERT/UPDATE/DELETE, no flush/commit/rollback, and no row lock.

There is no production route wiring, public API change, OpenAPI change,
database schema change, or Alembic migration in 25K.
