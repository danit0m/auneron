# Work Manager Operations V1

**Status:** Frozen for Commit 22

## 1. Runtime signals

Every HTTP request has an `X-Request-ID`. Use that value to correlate the
generic `auneron.http` record with Work-domain records.

Successful mutation records use:

```text
logger           auneron.work
message          work_change_completed
event            work.change
request_id       request correlation ID
outcome          applied | replayed | unchanged
work_item_id     aggregate ID
scope_type       global | account | user
work_event_type  persisted domain event type
actor_type       user | agent | system | integration
actor_user_id    authenticated user ID when applicable
version          resulting aggregate version
```

The record intentionally excludes title, description, comment, context data,
event data, actor/origin reference and idempotency key. Do not add request or
response bodies to Work logs.

Rejected domain operations emit one of `work.access_denied`, `work.conflict`
or `work.request_rejected`. Unexpected HTTP-boundary failures emit
`work.internal_error` with status and exception type only. The raw exception
message is excluded.

## 2. Minimum monitoring

Monitor rates and latency by HTTP route/status and use request ID for drill
down. Suggested alerts are operational starting points, not universal SLOs:

| Signal | Investigate when |
|---|---|
| `work.internal_error` | any sustained occurrence or burst |
| HTTP 503 on `/work-items` | database readiness also fails or rate exceeds baseline |
| `work.conflict` | version/idempotency conflicts rise above client retry baseline |
| `work.access_denied` | unusual user, scope or route pattern |
| `outcome=replayed` | retry rate changes abruptly |
| POST/PATCH/DELETE latency | p95 materially exceeds normal transaction latency |
| PostgreSQL advisory-lock wait | dependency mutations queue or time out |

Do not infer a security incident from a single 403/404. Preserve request IDs
and compare authentication, HTTP and Work-domain signals before escalation.

## 3. Paging runbook

Historical collection endpoints default to 50 and reject limits above 100.
When a response has `next_cursor`, repeat the request with
`after_id=<next_cursor>`. Stop when `next_cursor` is null.

An empty Memory-link page can still have a next cursor because inaccessible
Memory references are removed after the bounded database scan. Continue until
the cursor is null.

## 4. Migration and deploy

Commit 22 has two ordered migrations:

```text
4d8c2a1f7b90
  -> c2f7a9d4e681  Work foundation
  -> d7b3e5f1a902  Lifecycle and recurrence
```

Before deploy:

1. back up the target PostgreSQL database;
2. run `python -m alembic upgrade head`;
3. confirm `python -m alembic current` reports `d7b3e5f1a902`;
4. run `python -m alembic check`;
5. smoke-test authentication plus one authorized Work list;
6. confirm `Cache-Control: no-store` and `X-Request-ID`;
7. verify one controlled mutation produces `work.change` without payload data.

## 5. Rollback

Prefer application rollback with the migrated schema left in place when the
prior application version tolerates the new tables. If a schema downgrade is
required, stop Work writes, back up the database, and test the downgrade on a
restored copy first.

The downgrade from `d7b3e5f1a902` removes recurrence tables/columns; the
downgrade from `c2f7a9d4e681` removes all Work tables. Both are destructive to
Work data. Never automate those downgrades against production without an
explicit data-loss decision.

## 6. Deferred operations

Commit 22 does not include a scheduler, notification worker, external outbox,
metrics backend, cryptographic audit ledger or automatic execution. Recurrence
generation remains an explicit authorized request. Approval and autonomous
execution policy belong to later commits.
