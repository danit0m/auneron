# Work Manager Lifecycle Contract V1

**Status:** Frozen for Commit 22

## 1. Transition matrix

| Current | Allowed targets |
|---|---|
| `backlog` | `ready`, `cancelled` |
| `ready` | `backlog`, `in_progress`, `cancelled` |
| `in_progress` | `blocked`, `completed`, `cancelled` |
| `blocked` | `in_progress`, `cancelled` |
| `completed` | none |
| `cancelled` | none |

`blocked` and `cancelled` require a non-blank reason. The first transition to
`in_progress` records `started_at`; later unblock transitions preserve it.
Completion and cancellation set exactly one matching terminal timestamp.

An active recurrence rule must be disabled before its template becomes
terminal. Terminal items reject details, priority, assignee, schedule and
dependency changes. Comments and system notes remain permitted because they
append audit context without changing the terminal business result.

## 2. Dependency direction and types

For an edge `(work_item_id, depends_on_work_item_id)`, the first item is the
dependent and the second is its predecessor.

| Type | Gate |
|---|---|
| `finish_to_start` | predecessor must be completed before dependent starts |
| `start_to_start` | predecessor must have started before dependent starts |
| `finish_to_finish` | predecessor must be completed before dependent completes |
| `start_to_finish` | predecessor must have started before dependent completes |

An edge may be inserted or removed only while the dependent is in `backlog` or
`ready`. Both items must share exact `(scope_type, account_id,
subject_user_id)` identity. Self-reference, duplicate pairs and transitive
cycles are rejected.

## 3. Concurrency and audit

Every edge mutation acquires the graph advisory lock, locks the dependent item,
validates idempotency and expected version, checks lifecycle/scope/cycle rules,
changes the edge, increments the item version and appends the corresponding
event in one transaction.

The lock serializes reverse concurrent edges, so only one side can commit when
the pair would create a cycle. A failure at any step leaves neither an edge nor
a version/event change.

## 4. Event contract

Every successful transition emits `status_changed`. Edge insertion emits
`dependency_added`; edge removal emits `dependency_removed`. Event data records
the version interval and compact normalized changes. Equivalent idempotent
retries return the original event without another version increment.
