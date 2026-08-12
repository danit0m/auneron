# Memory / Knowledge Compatibility

**Status:** Frozen compatibility decision after 21B.1 audit

## 1. Existing `knowledge` subsystem

Before Memory V1, Auneron already has a `knowledge` table and `KnowledgeService`.

It is used as an operational stream of agent-generated insights, risks, recommendations, alerts and notifications.

Its current lifecycle is centered on:

```text
resolved = false / true
reopen
delete
```

and it is consumed by existing Brain/Executive behavior.

## 2. Why it is not Memory V1

The existing subsystem does not provide the frozen Memory V1 semantics for:

```text
confidence
importance
valid_from / valid_until
provenance model
memory_key
supersession chain
evidence
scope model
append-only knowledge history
keyset recall
Memory RBAC
```

It also owns commits directly and supports physical delete, which intentionally differs from the Memory V1 contract.

Therefore `KnowledgeService` is not renamed or repurposed as `MemoryService`.

## 3. Coexistence rule

During Commit 21 phases 21B–21F:

```text
knowledge            memory_items / memory_evidence
(existing)           (new)
     │                       │
     ├─ current agents       ├─ MemoryService
     ├─ Brain routes         ├─ MemoryRepository
     └─ Executive service    └─ Memory API/RBAC
```

Both coexist.

No existing `knowledge` record is automatically migrated in 21B.

No current agent is changed to dual-write during the database/service construction phases.

No existing `knowledge` endpoint is removed during Commit 21.

## 4. Integration point — 21G

Only after Memory persistence, service, retrieval, API/security and tests are green may Brain/agents begin integration.

The initial integration should be a controlled adapter:

```text
Knowledge
    ↓
Knowledge → Memory adapter
    ↓
MemoryService.remember()
    ↓
memory_items / memory_evidence
```

The adapter must not write directly to Memory models/repository.

## 5. Promotion policy

Not every Knowledge row automatically deserves durable Memory.

Promotion must be policy-driven.

Examples:

```text
agent insight
→ possibly observation

risk/recommendation
→ possibly observation or decision context

notification
→ usually operational event, not automatically durable memory
```

The exact mapping belongs to 21G and must be tested.

## 6. Provenance

When a Knowledge item is promoted to Memory, provenance must preserve the source record.

Recommended logical reference:

```text
source_type = derived
source_reference = knowledge:<knowledge_id>
```

Agent identity can be preserved as complementary context/evidence.

This prevents the promoted record from pretending it came directly from the database when it actually came through the legacy Knowledge layer.

## 7. One-way V1 bridge

The initial bridge is one-way:

```text
knowledge → memory
```

Memory lifecycle changes do not automatically mutate `knowledge.resolved`, and resolving/reopening Knowledge does not automatically mutate Memory.

Automatic synchronization is deferred until there is an explicit business policy.

## 8. No deprecation in Commit 21

`knowledge` cannot be removed while it remains used by agents, Brain routes or Executive Service.

Deprecation requires a later dedicated migration plan with:

- dependency inventory;
- replacement APIs;
- data policy;
- tests;
- backward compatibility;
- release/rollback strategy.

## 9. Schema compatibility

The 21B.1 audit confirms:

```text
accounts.id = INTEGER
users.id    = INTEGER
```

Therefore Memory scope/user foreign keys use `INTEGER`.

Memory-owned identifiers may use `BIGINT`.

## 10. Alembic registration

Auneron uses one declarative `Base`.

Alembic imports `app.models`, whose `__init__.py` explicitly imports model classes.

New Memory models must be exported there before autogenerate/schema comparison.

## 11. Timestamp compatibility

Existing models consistently use timezone-aware creation timestamps with server-side `now()`.

No existing `updated_at` convention was found.

Memory introduces its own explicit V1 `updated_at` convention without changing existing models.
