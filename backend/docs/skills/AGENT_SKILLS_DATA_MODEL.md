# Agent Skills Data Model V1

**Status:** Extended through Commit 23C

## 1. Tables

### `skills`

Stable catalog identity.

| Field | Contract |
| --- | --- |
| `skill_key` | unique lowercase key without surrounding whitespace |
| `provider` | publisher namespace, not a credential |
| `display_name` | human-readable nonblank name |
| `description` | nonblank purpose and behavior summary |
| `status` | `active`, `disabled` or `retired` |
| `created_by_user_id` | nullable audit attribution |

Catalog deletion is restricted while versions exist. User deletion sets audit
attribution to null and preserves the catalog.

### `skill_versions`

Exact executable contract.

| Field | Contract |
| --- | --- |
| `version` | nonblank publisher version |
| `runtime_kind` | `internal_python` or `plugin` |
| `handler_reference` | nonblank implementation locator |
| `execution_mode` | `read_only`, `mutating` or `external` |
| `manifest_digest` | lowercase 64-character digest |
| `manifest` | canonical non-secret manifest JSON |
| `input_schema` / `output_schema` | bounded JSON contracts |
| `timeout_seconds` | 1 through 300 |
| `max_output_bytes` | 1 KiB through 1 MiB |
| `status` | `draft`, `published` or `retired` |

Publication timestamps must match the status. `(skill_id, version)` and
`(skill_id, manifest_digest)` are unique. Skill deletion is restricted.

### `skill_capabilities`

Resource declarations for an exact version.

| Field | Contract |
| --- | --- |
| `capability_key` | canonical lowercase resource name |
| `access_mode` | `read`, `write` or `execute` |
| `resource_scope` | `internal`, `account`, `user` or `external` |
| `required` | distinguishes mandatory from optional resources |

Duplicate declarations are rejected. Capabilities cascade only when their
own version is deliberately deleted; published-version deletion will be
prohibited by the 23B service contract.

### `agent_skill_bindings`

Deterministic discovery link from a legacy/current agent name to one exact
version.

| Field | Contract |
| --- | --- |
| `agent_name` | nonblank registry identity |
| `skill_version_id` | pinned version, never a floating alias |
| `priority` | integer from 1 through 1000 |
| `enabled` | discovery switch only |
| `configuration` | non-secret JSON configuration |

Duplicate agent/version bindings are rejected. A bound version cannot be
deleted. Deleting an audit user sets attribution to null.

### `skill_invocations`

Append-preserving runtime ledger for one exact published version.

| Field | Contract |
| --- | --- |
| `skill_version_id` | exact invoked version; delete is restricted |
| `actor_type` / `actor_reference` | durable attribution scope |
| `actor_user_id` | nullable user audit FK with `SET NULL` |
| `idempotency_key` | optional canonical key; required by runtime for mutating/external calls |
| `request_fingerprint` | SHA-256 identity of version, actor and canonical input |
| `input_digest` | SHA-256 of canonical input; raw input is not retained |
| `status` | `running`, `succeeded`, `failed`, `timed_out` or `rejected` |
| `output_payload` | successful replay envelope only |
| `output_digest` / `output_bytes` | successful canonical output metadata |
| `error_code` | bounded sanitized terminal code |
| `duration_ms` | nonnegative terminal duration |
| `started_at` / `finished_at` | timezone-aware lifecycle timestamps |

The terminal-state constraint prevents a successful row from carrying an error
and prevents failed/rejected/timed-out rows from carrying output payloads.

## 2. Indexes

Indexes support:

- catalog lookup by status and key;
- version resolution by skill/status and runtime;
- policy lookup by capability/mode/scope;
- deterministic enabled bindings by agent and priority;
- reverse binding lookup by version;
- invocation history by version, actor and status.

## 3. Delete and retention rules

```text
skill -> version          RESTRICT
version -> capability    CASCADE
version -> binding       RESTRICT
version -> invocation    RESTRICT
user -> audit fields     SET NULL
user -> invocation actor SET NULL
```

Hard deletion is not an API operation. Retiring or disabling records is the
normal lifecycle. 23C invocation history is retained independently from
bindings and prevents deletion of an invoked version.

## 4. Schema evolution

Alembic revision `e4a6c8d2f913` follows `d7b3e5f1a902` and creates the four
Agent Skills catalog tables.

Alembic revision `f6c9a1d4b702` follows `e4a6c8d2f913` and creates only
`skill_invocations` plus its indexes and constraints. Downgrading 23C removes
the invocation ledger and restores the exact 23B schema head.
