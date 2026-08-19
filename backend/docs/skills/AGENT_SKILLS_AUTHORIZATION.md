# Agent Skills Authorization Contract V1

**Status:** Gate 23D

## 1. Purpose

Gate 23D turns the 23C runtime into an explicit user-initiated API without
turning catalog metadata into authority.

The protected flow is:

```text
X-API-Key
  -> authenticated user session
  -> base Skill execution RBAC
  -> exact published/active version resolution
  -> execution-mode policy
  -> capability scope policy
  -> anti-IDOR resource checks
  -> session-derived runtime actor
  -> SkillRuntimeService.invoke
```

No authorization failure creates a `skill_invocations` row because policy is
resolved before the runtime is entered.

## 2. User execution permissions

23D adds four central permissions:

| Permission | Meaning |
| --- | --- |
| `skill:execute` | invoke an eligible read-only version explicitly |
| `skill:execute_mutating` | invoke an eligible mutating version explicitly |
| `skill:execute_external` | invoke an eligible external version explicitly |
| `skill:execute_user_scope` | operate on another active user's declared user scope |

Role assignment is intentionally conservative:

| Role | Read-only | Mutating | External | Cross-user |
| --- | --- | --- | --- | --- |
| viewer | no | no | no | no |
| analyst | yes | no | no | no |
| manager | yes | yes | no | no |
| executive | yes | yes | no | no |
| administrator | yes | yes | yes | yes |
| developer | yes | yes | yes | yes |

External execution additionally requires a currently elevated session.

## 3. Capability declarations are not grants

`SkillCapability` rows are publisher declarations. 23D independently checks all
declared rows before runtime execution, including `required=false` rows.

This conservative rule is necessary because Commit 23 has no per-invocation
capability attenuation mechanism. A handler registered for the version could
otherwise use an optional declared resource after policy ignored it.

A `read_only` version may declare only `read` capabilities. A capability with
`resource_scope=external` requires `execution_mode=external`. Inconsistent
published declarations are rejected as invalid executable state.

## 4. Bound resource identifiers

Authorization and execution must refer to the same resource identity.

For this reason 23D reserves these top-level `input_payload` fields:

- `account_id` for any `resource_scope=account` declaration;
- `subject_user_id` for any `resource_scope=user` declaration.

If the version declares the corresponding scope, the field is mandatory and
must be a positive integer. If the version does not declare the scope, use of
the reserved field is rejected.

The exact authorized payload is then passed unchanged to the runtime. This
prevents a confused-deputy pattern where a caller authorizes account A in a
separate envelope but asks the handler to act on account B in its executable
input.

The V1 convention intentionally supports one account and one subject user per
invocation. A future requirement for multiple independently authorized
resources needs an explicit versioned capability-binding model rather than an
implicit list convention.

## 5. Account scope

Account-scoped capability access maps to the existing client authority model:

- if every account capability is `read`, `clients.view` is required;
- if any account capability is `write` or `execute`, `clients.manage` is
  required;
- the referenced account must exist.

Missing permission and missing account both become an opaque not-found result
after the caller has passed the base Skill execution permission. This avoids
resource enumeration.

## 6. User scope

A user may execute an otherwise authorized user-scoped Skill on their own
`subject_user_id`.

Cross-user execution requires `skill:execute_user_scope` and the target user
must exist and be active. Failure is opaque not-found to avoid user
enumeration.

## 7. External scope and recent authentication

A version with `execution_mode=external`, or an external capability, is the
highest-risk explicit execution class in Commit 23.

23D requires:

1. `skill:execute`;
2. `skill:execute_external`;
3. a currently elevated authenticated session;
4. the existing runtime idempotency key.

Session elevation is recent-authentication evidence only. It is not business
approval. Human approval policy and autonomous execution remain Commit 24.

## 8. HTTP actor integrity

The public request contains only the executable `input_payload`. The client
cannot send `actor_type`, `actor_reference` or `actor_user_id`.

The route derives:

```text
actor_type      = user
actor_reference = user:{authenticated_user_id}
actor_user_id   = authenticated_user_id
```

The 23C runtime continues to treat these fields as ledger attribution. 23D is
the layer that establishes their authenticated origin for this HTTP path.

## 9. API surface

23D exposes exactly:

```text
POST /agent-skills/versions/{version_id}/invoke
```

The `Idempotency-Key` header is forwarded to the 23C runtime. Mutating and
external versions therefore retain the runtime's mandatory idempotency rule.

23D deliberately exposes no catalog mutation endpoint and no invocation-history
endpoint. Catalog lifecycle remains an internal service boundary, and the
current invocation ledger does not contain durable account/user scope columns
suitable for a public anti-IDOR history query.

## 10. Error and data-handling rules

Skill HTTP responses use a frozen error envelope with request ID and
`Cache-Control: no-store`.

Authorization logs include only operational metadata such as user ID, version
ID, outcome class and presence of account/user scope. They do not log raw
input, output, idempotency keys, credentials or exception text.

Unauthorized scope resolution never reaches the runtime and therefore never
creates a ledger row.

## 11. Deferred authority

23D is explicit human-user execution only.

It does not authorize an agent, orchestrator, Work item, Memory item, model
plan, plugin response or capability declaration to execute by itself.
Sensitive-action approval, autonomous selection and non-user actor authority
belong to Commit 24.
