# Work Manager Threat Model V1

**Status:** Frozen for Commit 22

## 1. Protected assets

- work scope and ownership;
- task state, priority, assignee and deadlines;
- dependency graph integrity;
- audit-event integrity;
- Memory System references;
- provenance and idempotency keys;
- authorization and approval boundaries.

## 2. Main threats and controls

| Threat | Domain control | Commit 22 boundary control |
|---|---|---|
| Cross-scope disclosure or mutation | explicit scope columns and DB integrity | scope policy and opaque IDOR tests |
| Duplicate work from retries | scope-unique `work_key` plus 22B payload fingerprint | `Idempotency-Key` contract |
| Lost update from concurrent actors | 22B row lock plus optimistic version | sanitized 409 mapping |
| Invalid lifecycle state | constraints plus 22C allow-listed matrix | operation RBAC and 409 mapping |
| Silent blocking-state corruption | reason constraint plus dependency gates | correlated mutation telemetry |
| Direct or transitive dependency cycle | DB self-check plus 22C recursive analysis | conflict telemetry and runbook alert |
| Concurrent graph cycle | transaction advisory lock | database contention monitoring |
| Duplicate dependency edge | DB uniqueness plus conflict mapping | secure dependency routes |
| Duplicate audit event | 22B key uniqueness and replay fingerprint | explicit replay outcome telemetry |
| Audit attribution loss | 22B actor validation and stable reference | session-derived actor binding |
| Memory context deletion | memory FK uses RESTRICT | dual Work/Memory authorization |
| Prompt injection in linked memory | memory remains data, not authority | 23/24 agent and approval policy |
| Unauthorized autonomous execution | no execution capability in 22A | Commit 24 approval boundary |
| Payload amplification via JSON | 22B object, 32 KB and depth-five limits | 512 KB request limit |
| Read amplification from history | indexed append-only collections | 100-record cursor pages |
| Sensitive data in operational logs | payload data remains outside authority | metadata allow-list plus formatter redaction |
| Destructive task deletion | no Work-item delete API | terminal cancellation lifecycle |
| Duplicate recurring work | occurrence uniqueness, deterministic key and locking | worker retry telemetry |
| Timezone scheduling drift | aware inputs, UTC persistence, IANA wall-clock advance | timezone database updates |

## 3. Trust boundaries

Neither `context_data`, `event_data`, `origin_reference`, linked memory content,
nor agent-generated text grants permission or authorizes execution.

Only authenticated identity, RBAC, scope policy and service invariants can
authorize a Work Manager mutation.

## 4. Dependency graph risk

PostgreSQL constraints prevent direct self-dependency and duplicate edges but
cannot prevent longer cycles such as A -> B -> C -> A. 22C uses one
transaction-scoped PostgreSQL advisory lock for every graph mutation, then
performs recursive reachability analysis before inserting an edge.

## 5. Event log limitations

Events are append-oriented by schema and repository design: the
repository exposes insert and read operations but no update/delete method.
This is not a cryptographic ledger. Operational tamper evidence belongs to
later governance work.

## 6. Failure behavior

Current-state mutation and its event must commit or roll back together. The
same rule covers dependency edges and every generated recurrence record. A
partial success is invalid.

External notifications, agent execution and integrations occur only after the
database transaction completes. Delivery uses outbox/event infrastructure in a
later commit; 22B does not claim reliable external delivery.

## 7. Security tests implemented in Commit 22

- IDOR across global/account/user scope;
- unauthorized assignment and scope escalation;
- hidden-resource 404 behavior;
- optimistic concurrency conflicts;
- dependency-cycle and concurrent-edge races;
- event idempotency;
- payload size/depth abuse;
- linked-memory authorization;
- prompt-injection content treated only as data;
- absence of physical-delete routes;
- sensitive exception sanitization;
- cache-control for protected responses.
- bounded historical collections and cursor continuation;
- mutation/replay telemetry with request correlation;
- absence of Work payloads and idempotency keys in domain logs;
- formatter redaction for idempotency and credential fields.
