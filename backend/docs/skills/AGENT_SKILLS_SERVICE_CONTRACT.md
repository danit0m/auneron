# Agent Skills Service Contract V1

**Status:** Frozen for Commit 23B

## 1. Ownership boundaries

`SkillRepository` performs SQLAlchemy statements and `flush` only. It never
calls `commit`, `rollback`, `begin` or `begin_nested`. `SkillService` owns each
catalog transaction and converts persistence conflicts into domain errors.

The service is a catalog boundary. It does not import, resolve or execute a
handler and does not grant permissions from manifest or capability data.

## 2. Draft contract

A draft is created only beneath an active `SkillDefinition`. The service
normalizes and validates:

- the publisher version and runtime kind;
- a syntactically bounded handler reference;
- the execution mode, timeout and output ceiling;
- manifest, input schema and output schema JSON objects;
- JSON size, depth, serialization and non-secret manifest keys.

The caller never supplies `manifest_digest`. The service calculates SHA-256
over canonical UTF-8 JSON containing the complete executable contract:

```text
version + runtime + handler + execution mode + manifest
+ input schema + output schema + timeout + output ceiling
```

Canonical JSON uses sorted keys, compact separators, UTF-8 and rejects NaN or
infinite values. Equivalent object key ordering therefore produces the same
digest, while any executable-contract change produces a different digest.

Draft replacement requires a row lock and recalculates the digest. Draft
deletion is permitted only through the service and only while no binding
references the version.

## 3. Atomic publication

Publication locks the exact version and its parent skill. It succeeds only
from `draft` while the parent skill is active. Capability declarations are
normalized, deduplicated and bounded to 64 entries before persistence.

In one transaction, the service:

1. replaces any draft capability rows;
2. writes the complete normalized capability set;
3. changes the version to `published`;
4. records an aware UTC `published_at` timestamp;
5. commits once.

Any validation or database failure rolls back the whole publication. No
partially published version or partial capability set may remain.

## 4. Published immutability

`replace_draft_contract` and `delete_draft_version` reject `published` and
`retired` versions with `SkillImmutableError`. Capabilities have no public
mutation method after publication. A behavior change requires another exact
version and digest.

The only permitted published-version mutation is lifecycle retirement.
Retirement preserves contract data, records `retired_at` and disables every
enabled binding to that version in the same transaction.

## 5. Bindings and resolution

A binding may target only an exact published version beneath an active skill.
Agent name, priority and configuration are bounded and normalized. Secret-like
configuration keys are rejected because this catalog is not a secret store.

Resolution returns enabled bindings ordered by `(priority, id)` and filters
out disabled/retired catalog state. The result includes the pinned skill,
version and declared capabilities. It is discovery data only; RBAC and scope
authorization arrive in 23D and remain mandatory before execution.

## 6. Lifecycle

- active skills accept new drafts, publication and bindings;
- disabled skills retain history and disable active bindings;
- retired skills are terminal and cannot be reactivated;
- published versions retire but are never hard-deleted;
- retiring a version disables its bindings atomically.

## 7. Deferred work

23B performs no dynamic import, plugin call, input/output schema evaluation,
retry, timeout enforcement, invocation logging or HTTP exposure. Those
boundaries are introduced incrementally by 23C and 23D.
