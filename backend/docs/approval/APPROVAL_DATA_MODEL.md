# Approval Data Model V1

**Status:** Extended through Commit 24D

## 1. `approval_requests`

One durable request represents one exact proposed Skill execution.

| Field | Contract |
| --- | --- |
| `action_type` | currently only `skill_execution` |
| `skill_version_id` | exact published Skill version; delete is restricted |
| requester actor fields | durable proposal attribution |
| `idempotency_key` | mandatory bounded requester-scoped key |
| `request_fingerprint` | lowercase SHA-256 of exact action identity |
| `input_digest` | lowercase SHA-256 of canonical input; raw input absent |
| `risk_level` | `low`, `medium`, `high`, or `critical` |
| `required_permission` | human permission required to decide |
| `status` | `pending`, `approved`, `rejected`, `expired`, or `cancelled` |
| target IDs | safe scalar scope metadata only, never grants |
| `expires_at` / `resolved_at` | bounded lifecycle timestamps |

The unique identity
`(requester_actor_type, requester_reference, idempotency_key)` prevents one
requester from silently changing an action behind a reused key.

`medium` is reserved in the schema for later policy evolution. The 24A Skill
classifier emits `low`, `high`, or `critical`.

## 2. `approval_decisions`

One request may have at most one durable human decision.

| Field | Contract |
| --- | --- |
| `approval_request_id` | unique request FK; delete restricted |
| `decision` | `approved` or `rejected` |
| `decided_by_user_id` | nullable historical FK with `SET NULL` |
| `decided_by_reference` | durable user reference snapshot |
| `decided_by_role` | role snapshot at decision time |
| `permission_used` | exact approval permission used |
| `decision_note` | optional bounded note; never secrets |
| `created_at` | decision timestamp |

## 3. Delete and retention

```text
skill_version -> approval_request   RESTRICT
user -> requester audit user        SET NULL
user -> decider audit user          SET NULL
approval_request -> decision        RESTRICT
```

Requester/decider textual references and role/permission snapshots retain
audit meaning if a historical user record is later removed.

Target account/user IDs are scalar audit metadata, not ownership FKs. Their
current existence and authorization must be revalidated before eventual
execution.

## 4. Lifecycle integrity

A pending request has `resolved_at IS NULL`. Every terminal request has a
non-null `resolved_at`.

Decision rows are append-preserving. 24A has no mutation that reverses an
approved or rejected request.

## 5. Schema evolution

Alembic revision `b7d4e2a6c915` follows `f6c9a1d4b702` and creates only
`approval_requests`, `approval_decisions`, their constraints and indexes.
Downgrade removes only those two tables.

## 5. `approval_consumptions`

24D adds one durable consumption row per approval request/decision. It records
the non-human consumer, current authority-user snapshot, deterministic runtime
idempotency key, approved request fingerprint/input digest and an optional
SkillInvocation link.

Lifecycle:

- `reserved`: committed before runtime, no invocation link yet;
- `consumed`: linked to exactly one invocation, regardless of runtime success
  or terminal runtime failure;
- `failed`: runtime failed before any invocation ledger existed.

Unique constraints prevent one ApprovalRequest, ApprovalDecision or linked
SkillInvocation from being consumed through multiple rows.

`approval_decisions.sensitive_elevation_verified` records whether the critical
human decision crossed the elevated-session boundary. Existing pre-24D rows
default to `false` and therefore cannot authorize critical governed execution.
