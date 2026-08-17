# Work Manager Service Contract V1

**Status:** Frozen for Commit 22

## 1. Transaction ownership

`WorkManagerService` is the only transaction owner. `WorkRepository` may add,
load, lock, list, delete aggregate-owned dependency/link rows and flush, but it
must never call `commit`, `rollback`, `begin` or `begin_nested`.

Every successful mutation contains, in one PostgreSQL transaction:

1. `SELECT ... FOR UPDATE` on the `work_items` aggregate root;
2. idempotency replay validation when a key is present;
3. comparison of client `expected_version` with persisted `version`;
4. current-state mutation;
5. one and only one `version` increment;
6. insertion of the matching append-only `work_events` row;
7. commit.

Any exception rolls back both current state and event. External I/O is forbidden
inside this boundary.

## 2. Creation contract

Creation begins at version 1 and writes exactly one `created` event. The service
normalizes vocabulary, keys, text, scope shape, aware timestamps, actor identity
and JSON before persistence.

`work_key` is the idempotency identity for creation. A retry with the same scope,
key and normalized request fingerprint returns the existing item and event. The
same key with different content raises `WorkConflictError`.

An event `idempotency_key` on create is accepted only when `work_key` is also
present; a per-item event key alone cannot deduplicate two item inserts.

Parent and child must share the same `(scope_type, account_id,
subject_user_id)` identity.

## 3. Mutation contract

The service exposes these non-lifecycle mutations:

| Operation | Event | Current-state effect |
|---|---|---|
| replace details | `details_changed` | title, description, context |
| change priority | `priority_changed` | priority |
| change assignee | `assignee_changed` | assignee user or null |
| add comment | `comment_added` | version/timestamp plus event |
| add system note | `system_note` | version/timestamp plus event |
| change schedule | `schedule_changed` | due and SLA instants |

A no-op detail, priority or assignee request is rejected and does not increment
the version. Comments and notes are intentional audit mutations and therefore
do increment the aggregate version.

## 4. Optimistic concurrency

The row lock serializes writers. After acquiring it, the service compares
`expected_version` with the current row. A mismatch raises
`WorkVersionConflictError` carrying both values and leaves state unchanged.

Concurrent requests using the same expected version produce exactly one
success. Concurrent equivalent requests using the same idempotency key produce
one application and one replay result.

## 5. Event idempotency

Mutation idempotency keys are normalized and unique per item. Each event stores
a deterministic request fingerprint including item, expected version, event
type, actor and normalized request.

Same key plus same fingerprint returns the original event without another
version increment. Same key plus another fingerprint raises
`WorkIdempotencyConflictError`.

Dependency insertion and removal use the same item-level replay contract. Due
occurrence generation additionally persists an immutable occurrence identity;
an equivalent replay returns the original item, rule, occurrence and event.

## 6. Actor contract

Allowed actor types are `user`, `agent`, `system` and `integration`.

- `actor_reference` is always required and stable;
- a `user` actor requires a positive `actor_user_id`;
- non-user actors cannot carry `actor_user_id`;
- `system_note` requires a system actor;
- 22D binds public HTTP values to the authenticated principal and authorization
  decision; public request schemas contain no actor fields.

## 7. JSON and audit data

`context_data` and service-created `event_data` must be JSON objects, serialize
deterministically, remain within 32 KB and have maximum depth five.

Events store compact change metadata. Descriptions and context objects are
represented by before/after SHA-256 hashes to prevent audit-payload duplication
and accidental disclosure.

## 8. Lifecycle and dependency contract

Status transitions are allow-listed, versioned and audited. Block and cancel
operations require a reason. Start and completion gates evaluate typed
predecessor requirements inside the same locked transaction.

Dependency edges must share exact scope identity, cannot repeat or reference
the dependent item, and may change only in `backlog` or `ready`. A global
transaction-scoped advisory lock serializes graph cycle checks and insertion.
The complete matrix is in `WORK_MANAGER_LIFECYCLE_CONTRACT.md`.

## 9. Schedule, SLA and recurrence contract

Schedule inputs must carry timezone information and are normalized to UTC. An
SLA instant cannot follow the business deadline when both are present. SLA
evaluation and breach listing are read-only and bounded.

Recurrence configuration, disablement and generation are versioned event
mutations. Generation creates one occurrence work item and its `created` event,
an occurrence identity, a template `recurrence_generated` event and the rule
advance in one transaction. Details are in
`WORK_MANAGER_RECURRENCE_CONTRACT.md`.

## 10. HTTP authorization in 22D

22D owns RBAC, scope authorization, authenticated actor binding, secure HTTP
schemas/routes and error mapping. It also exposes transactional Memory link
and unlink operations. The route authorizes both the Work item and referenced
Memory item before calling the service; list responses omit links whose Memory
scope is not readable by the current principal.
