# Work Manager Authorization and HTTP Contract V1

**Status:** Frozen for Commit 22

## 1. Authentication boundary

Every `/work-items` route requires the service API key and a valid user
session. A request with only one credential is unauthenticated. The route never
accepts actor or origin authority from JSON, query parameters or headers.

The authenticated actor is always:

```text
actor_type      user
actor_reference user:<authenticated user id>
actor_user_id   <authenticated user id>
origin_type     api
origin_reference work-api:user:<authenticated user id>
```

System and agent actors remain internal service callers. No public endpoint
creates a `system_note` or lets a caller impersonate those actor types.

## 2. Operation permissions

| Operation | Permission |
|---|---|
| read item, list, events, SLA and recurrence | `work:read` |
| create | `work:create` |
| details, priority, assignee, status, schedule and Memory links | `work:update` |
| comment | `work:comment` |
| add or remove dependency | `work:manage_dependencies` |
| configure, disable or generate recurrence | `work:manage_recurrence` |
| assign another user | `work:assign` |

Viewer is read-only. Analyst may create, update and comment but cannot manage
dependencies, recurrence or third-party assignment. Manager and executive may
perform those operational actions. Administrator and developer also receive
privileged global and cross-user scope capabilities.

## 3. Scope policy

An operation permission is necessary but not sufficient.

| Scope | Read | Mutation |
|---|---|---|
| global | `work:read_global` | `work:manage_global` |
| account | existing account plus `clients.view` | existing account plus `clients.manage` |
| own user | base operation permission | base operation permission |
| other user | existing active user plus `work:read_user_scope` | existing active user plus `work:manage_user_scope` |

Malformed scope combinations return 422. A well-formed but missing or
inaccessible scope returns the same 404 as a missing Work item. This prevents
resource enumeration across scope boundaries.

Assignment does not change scope. Unassignment and self-assignment are allowed
when the caller can mutate the item. Assigning another user requires
`work:assign`, and the target must be an existing active user.

## 4. Related-resource authorization

Parent and dependency references are loaded through Work read authorization
before the service validates same-scope invariants. This prevents the API from
confirming an inaccessible Work ID.

A Memory link requires authorization to mutate the Work item and read the
referenced Memory item. Inactive Memory also requires `memory:history`.
Memory-link lists omit references that the current principal cannot read. The
linked content remains untrusted data and never grants authority.

## 5. HTTP mutation contract

Mutations carry a positive `expected_version` in the request body. A stale
version returns 409 `work_version_conflict`. Optional `Idempotency-Key` values
are limited to 255 characters and normalized by the service. Exact replay
returns the prior event without another version increment; reuse with another
request returns 409 `work_idempotency_conflict`.

Creation with an idempotency header also requires a scope-unique `work_key`.
The initial creation returns 201; a safe replay returns 200. Recurrence
generation follows the same 201/200 distinction.

No route physically deletes a Work item. `DELETE` is limited to dependency and
Memory-link subresources, each producing an audit event in the same transaction.

## 6. Error and cache contract

Every Work response, including errors, uses `Cache-Control: no-store`.
Protected errors use this envelope:

```json
{
  "error": {
    "code": "work_not_found",
    "message": "Trabalho não encontrado.",
    "request_id": "request-correlation-id"
  }
}
```

Validation details and unexpected exception text are not returned. Database
unavailability maps to a sanitized 503; other unexpected failures map to a
sanitized 500. Request bodies larger than 512 KB are rejected with 413.

## 7. Historical collection paging

Event, dependency, Memory-link and recurrence-occurrence lists accept:

```text
limit     default 50, minimum 1, maximum 100
after_id  optional positive cursor from the prior response
```

The response contains `items` and `next_cursor`. When `next_cursor` is not
null, pass it as the next `after_id`. Cursor scans use monotonically increasing
row IDs so concurrent appends do not repeat records already returned.

Memory-link authorization remains active on every page. A page may contain
fewer visible items than its limit when the principal cannot read one or more
linked Memory items; `next_cursor` still advances over the scanned window.
