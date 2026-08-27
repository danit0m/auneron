# Authenticated Advisory Proposals

## Purpose

`AuthenticatedAdvisoryProposal` is the durable, immutable persistence boundary
for an `AuthenticatedAdvisoryEnvelope`. Persisting a proposal does not grant
authority, create executable intent, create Work, execute a Skill, mutate an
Approval or Memory record, publish an EventBus event, or expose a public route.

## Durable identity

Idempotency is scoped by the exact triple:

`authority_user_id + auth_session_id + idempotency_key`

The idempotency key is stripped, lowercased and must match
`^[a-z0-9][a-z0-9._:-]{0,254}$`.

`request_id` is correlation metadata only. The first persisted request ID is
retained, but request ID is excluded from the semantic idempotency identity and
from the snapshot digest.

Authority user/session identifiers are durable scalar provenance references.
They deliberately are not foreign keys: the proposal must not retain or revive
an authentication session, and the persisted identifiers are never an
authorization grant.

## Snapshot protocol

Protocol: `authenticated_advisory_v1`.

The snapshot persists only:

- `decision_name`;
- ordered `selected_agents`;
- the matching ordered advisory agent entries;
- safe binding metadata: `binding_id`, `skill_version_id`, `skill_id`,
  `binding_priority`, `execution_mode`, and `runtime_kind`.

The snapshot never persists decision reason, confidence, signals, event name,
raw payload, role, permissions, scope, session elevation, credentials, tokens,
or executable intent.

The digest is SHA-256 over canonical JSON of
`[protocol, snapshot_payload]`. Canonical JSON uses sorted keys, compact
separators, UTF-8 and rejects non-finite numbers.

## Bounds

The durable contract fails closed above 32 selected agents, 512 advisory
bindings or 65536 canonical snapshot bytes. Decision and agent names are
bounded to 128 characters.

## Transaction and race behavior

Creation first reads by the exact durable identity. A row with the same digest
is returned as an idempotent duplicate. Reusing the identity for another digest
raises an idempotency conflict.

The service owns commit/rollback. The repository only executes statements and
flushes. If the unique constraint races, the service rolls back and re-reads
the exact identity. Same digest becomes a duplicate; different digest fails
closed.

## Future consumption boundary

No consumer may treat a stored proposal as current authority. Before any future
mutation or execution, the consumer must reload the current user and
authentication session, verify that both remain valid, reauthorize current
scope and the exact Skill, and fail closed for absent, stale, revoked, expired,
disabled, or unauthorized authority.

Work materialization, Approval bridging, governed Skill dispatch, public route
capture, and EventBus integration remain separate architecture checkpoints.
