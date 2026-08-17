# Work Manager Architecture V1

**Status:** Frozen for Commit 22

## 1. Purpose

Memory and work are separate domains:

```text
MEMORY
What do we know?

WORK MANAGER
What must be done?
```

A memory item is not a task. A task may reference memory as context, source,
decision or outcome without changing memory lifecycle rules.

## 2. Boundary

```text
Browser / Agent / Internal Component
                |
                v
        Authentication / RBAC
                |
                v
          WorkManagerService
        /       |          \
 validation  lifecycle  authorization
        \       |          /
                v
           WorkRepository
       /       |       |        \
    items  dependencies events  memory links
       \       |       |        /
                v
           SQLAlchemy
                |
                v
           PostgreSQL
```

Agents, Brain and Orchestrator must not access the Work Manager repository or
tables directly. They request operations through the service boundary.

## 3. Aggregates

`WorkItem` is the aggregate root. It owns current operational state.

`WorkDependency` represents a directed scheduling constraint between two work
items. Cycle detection belongs to the service because a row-level constraint
cannot prove graph acyclicity.

`WorkEvent` is an append-only audit record. It records domain changes but does
not replace the current state stored in `WorkItem`.

`WorkMemoryLink` connects work to existing memory without copying or mutating
memory content.

`WorkRecurrenceRule` is the persisted schedule of one template item.
`WorkRecurrenceOccurrence` is the immutable identity joining a rule occurrence
number, its scheduled instant and its generated work item.

## 4. Scope

The V1 scope model matches Memory:

```text
global   account_id=NULL, subject_user_id=NULL
account  account_id=required, subject_user_id=NULL
user     account_id=NULL, subject_user_id=required
```

Scope is an authorization boundary. Assignment is operational responsibility.
Changing `assignee_user_id` never changes the work item's scope.

## 5. Hierarchy

Projects and milestones use the same aggregate and may be parents of other work
items through `parent_work_item_id`.

V1 forbids direct self-parenting in PostgreSQL. Full ancestor-cycle prevention
is a service responsibility in 22B/22C.

## 6. Lifecycle

Initial states:

```text
backlog -> ready -> in_progress -> completed
                       |
                       v
                    blocked

non-terminal states -> cancelled
```

The exact transition matrix is frozen in
`WORK_MANAGER_LIFECYCLE_CONTRACT.md`. Terminal items reject business-field,
schedule and dependency mutations, while comments and system notes remain
available for append-only audit context.

Database invariants already enforce:

- blocked state requires a current blocked reason;
- cancellation requires a reason and cancellation timestamp;
- completion requires completion timestamp;
- active execution states require `started_at`;
- terminal timestamps cannot contradict each other;
- `version` is always positive.

## 7. Concurrency

`WorkItem.version` supports optimistic concurrency. Every mutating service
operation must compare an expected version, lock or atomically update the row,
increment the version exactly once, append the corresponding event, and commit
as one transaction.

22B/22C implement this rule with `SELECT ... FOR UPDATE`. A replay carrying the
same idempotency key and request fingerprint returns the original event without
changing the version again. A different request using that key is rejected.

Dependency graph mutations also acquire a PostgreSQL transaction-scoped
advisory lock before cycle analysis. This serializes competing graph edits and
prevents two individually valid concurrent edges from closing a cycle.

## 8. Idempotency

`work_key` is a stable, optional key unique inside global, account or user
scope. It prevents duplicate work creation from retries or external events.

`WorkEvent.idempotency_key` is optional and unique per work item. It prevents
the same domain event from being appended twice.

## 9. Transaction rule

One database transaction contains the current-state mutation, dependency or
memory-link mutation, and audit event.

No AI call, HTTP request, message delivery or other slow external operation may
run inside that transaction.

The repository only adds, loads, locks, lists and flushes persistence objects.
It never calls `commit`, `rollback` or opens a nested transaction. This keeps a
single transaction owner at the service boundary.

## 10. Authority rule

```text
Agent or model output
        |
        v
proposal / requested operation
        |
        v
policy + permission + service invariant
        |
        v
authorized state change
```

Creating a Work Item does not authorize executing the work. Sensitive-action
approval, risk classification and autonomous execution belong to Commit 24.

## 11. Scheduling and recurrence

Deadlines, SLA instants and recurrence schedules are timezone-aware and stored
as `TIMESTAMPTZ`. SLA evaluation is a read operation and never mutates state or
emits an event.

Recurrence generation is explicit and database-backed. It locks the template
and rule, creates one deterministic work item, appends both creation and
template audit events, advances or closes the rule and records the occurrence
identity in one transaction. No background worker or external I/O is hidden in
the service.

## 12. Secure HTTP boundary

The `/work-items` router requires both the service API key and an authenticated
user session. Route dependencies enforce the operation permission before the
route loads a resource. Scope authorization then evaluates global, account or
user visibility and maps an inaccessible scope to the same 404 as a missing
item.

Requests never accept actor type, actor reference, actor user ID, origin type
or origin reference. The route derives a `user:<id>` actor and an API origin
from the authenticated session. Assignment to another active user requires a
separate capability. Parent, dependency and Memory references receive their
own authorization checks before the service mutation begins.

Protected responses use `Cache-Control: no-store`. The HTTP boundary limits
request bodies, returns stable error envelopes bound to the request ID and
does not disclose validation details or unexpected exception text.

Historical collection routes return at most 100 records per request. Event,
dependency, Memory-link and recurrence-occurrence collections use an integer
`after_id` cursor and return `next_cursor` when another page exists.

## 13. Observability boundary

Every applied, replayed or unchanged Work mutation emits one structured
`work.change` record correlated with `X-Request-ID`. The record contains only
operational metadata: outcome, Work ID, scope type, domain event type, actor
type/user ID and resulting version.

Titles, descriptions, comments, context/event payloads, actor/origin
references and idempotency keys are not logged. The JSON formatter also
redacts any field whose key contains `idempotency` or `credential` if a future
caller accidentally includes it.

HTTP logs continue to provide method, path, status and duration. Combining
HTTP and domain logs by request ID distinguishes access denial, conflict,
replay, successful mutation and unexpected infrastructure failure without
using a second persistence transaction.

## 14. Non-goals in Commit 22

- no scheduler worker or automatic recurrence polling;
- no notifications;
- no automatic task execution;
- no approval policy;
- no frontend;
- no physical-delete endpoint.

Commit 22 exposes controlled HTTP operations but no autonomous execution authority.
Sensitive-action approval and agent autonomy remain outside Commit 22.
