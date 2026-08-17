# Work Manager Data Model V1

**Status:** Frozen for Commit 22

## 1. Tables

```text
work_items 1 ----- 0..N work_dependencies
work_items 1 ----- 0..N work_events
work_items 1 ----- 0..N work_memory_links N ----- 1 memory_items
work_items 1 ----- 0..1 work_recurrence_rules
work_recurrence_rules 1 ----- 0..N work_recurrence_occurrences
work_recurrence_occurrences N ----- 1 work_items
```

## 2. `work_items`

The aggregate root stores current operational state.

| Field | PostgreSQL type | Null | Rule |
|---|---|---:|---|
| `id` | `BIGINT` | no | primary key |
| `work_type` | `VARCHAR(24)` | no | task/project/milestone |
| `title` | `VARCHAR(240)` | no | non-blank |
| `description` | `TEXT` | yes | non-blank when present |
| `work_key` | `VARCHAR(255)` | yes | scope-unique idempotency key |
| `scope_type` | `VARCHAR(20)` | no | global/account/user |
| `account_id` | `INTEGER` | yes | account scope FK |
| `subject_user_id` | `INTEGER` | yes | user scope FK |
| `parent_work_item_id` | `BIGINT` | yes | hierarchy self-FK |
| `created_by_user_id` | `INTEGER` | yes | author FK |
| `assignee_user_id` | `INTEGER` | yes | responsible user FK |
| `status` | `VARCHAR(24)` | no | default backlog |
| `priority` | `VARCHAR(16)` | no | default normal |
| `blocked_reason` | `TEXT` | yes | required only while blocked |
| `status_reason` | `TEXT` | yes | cancellation reason required |
| `status_changed_at` | `TIMESTAMPTZ` | no | lifecycle timestamp |
| `due_at` | `TIMESTAMPTZ` | yes | business deadline |
| `sla_due_at` | `TIMESTAMPTZ` | yes | SLA deadline |
| `started_at` | `TIMESTAMPTZ` | yes | execution start |
| `completed_at` | `TIMESTAMPTZ` | yes | completion timestamp |
| `cancelled_at` | `TIMESTAMPTZ` | yes | cancellation timestamp |
| `version` | `INTEGER` | no | optimistic lock, default 1 |
| `origin_type` | `VARCHAR(24)` | no | provenance class |
| `origin_reference` | `VARCHAR(500)` | no | logical provenance |
| `context_data` | `JSONB` | no | non-authoritative metadata |
| `created_at` | `TIMESTAMPTZ` | no | creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | no | application-managed update |

Allowed work types:

```text
task project milestone
```

Allowed states:

```text
backlog ready in_progress blocked completed cancelled
```

Allowed priorities:

```text
low normal high urgent
```

Allowed origin types:

```text
user agent system api integration
```

## 3. Scope-unique work keys

Partial unique indexes keep retry identity stable across terminal states:

```text
global:  UNIQUE(work_key)
account: UNIQUE(account_id, work_key)
user:    UNIQUE(subject_user_id, work_key)
```

Each index applies only when its matching scope is active and `work_key` is not
null. Reusing a completed or cancelled work key is forbidden. Recurring work
must create deterministic occurrence keys.

## 4. `work_dependencies`

`work_item_id` is the dependent item and `depends_on_work_item_id` is its
predecessor.

Allowed types:

```text
finish_to_start
start_to_start
finish_to_finish
start_to_finish
```

The pair is unique and cannot directly reference itself. Both work-item FKs use
`ON DELETE CASCADE` because dependency edges are part of the work aggregate.
Graph-cycle validation remains mandatory in the service.

## 5. `work_events`

Events are append-only and ordered by `(created_at, id)` for each work item.

Initial event vocabulary:

```text
created
details_changed
status_changed
priority_changed
assignee_changed
schedule_changed
dependency_added
dependency_removed
memory_linked
memory_unlinked
comment_added
system_note
recurrence_configured
recurrence_disabled
recurrence_generated
```

Actor types:

```text
user agent system integration
```

`actor_reference` remains required even when a user FK is later set to null.
`event_data` contains structured event details and is not an authorization
source. `(work_item_id, idempotency_key)` is unique when a key is provided.

## 6. `work_memory_links`

Allowed relations:

```text
context source decision outcome
```

`(work_item_id, memory_id, relation)` is unique. Work deletion cascades its
links. Memory deletion is restricted so a link cannot silently lose its
business context.

## 7. `work_recurrence_rules`

Each template item has at most one persisted rule. Frequency is `daily`,
`weekly` or `monthly`; `interval_value` is between 1 and 365. `timezone_name`
stores a non-blank IANA identifier validated by the service.

`starts_at`, optional `ends_at`, `next_occurrence_at` and
`last_occurrence_at` are timezone-aware. An active rule always has a next
instant; an inactive rule never does. The next instant cannot precede the
start, exceed the end or fail to follow the last occurrence.

`max_occurrences` is optional and positive. `generated_occurrences` starts at
zero and cannot exceed that maximum. Optional `sla_lead_minutes` is between
zero and 525600 and derives an occurrence SLA from its scheduled deadline.

## 8. `work_recurrence_occurrences`

An occurrence row records `recurrence_rule_id`, positive
`occurrence_number`, generated `work_item_id` and `scheduled_for`. Rule/number,
rule/scheduled instant and generated item are independently unique. These
constraints make generation retry-safe even if a caller loses its response.

Generated keys use `<template-key>:occ:<number>` and remain subject to the
scope-unique work key constraints.

## 9. Foreign-key delete behavior

```text
scope account/user       RESTRICT
parent work item         RESTRICT
creator/assignee user    SET NULL
dependency work items    CASCADE
event work item          CASCADE
event actor user         SET NULL
memory-link work item    CASCADE
memory-link memory       RESTRICT
recurrence rule item     CASCADE
recurrence creator user  SET NULL
occurrence rule/item     CASCADE
```

No Commit 22 API will expose physical deletion. Terminal lifecycle states are
used instead.

## 10. Main indexes

```text
(status, priority, due_at, id)
(account_id, status, due_at)
(subject_user_id, status, due_at)
(assignee_user_id, status, priority, due_at)
(parent_work_item_id, status)
(origin_type, origin_reference)
```

Open deadlines use a partial index where status is not completed/cancelled and
`due_at` is present.

Dependencies are indexed by predecessor and type. Events are indexed by item
timeline, event type and actor. Memory links are indexed by memory and relation.
Active rules are indexed by next occurrence. Occurrence history is indexed by
scheduled instant and id.

## 11. JSON limits

`context_data` and `event_data` are `JSONB NOT NULL DEFAULT '{}'`.

22B service rules require an object root, deterministic serialization, maximum
32 KB payload and maximum depth 5. Data required for filtering, authorization
or integrity remains in normal columns.

Service-generated events keep request fingerprints and compact change metadata.
Large descriptions and contexts are represented by hashes in the event rather
than duplicated into the audit payload.
