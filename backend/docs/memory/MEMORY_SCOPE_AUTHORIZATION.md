# Memory Scope Authorization V1

**Status:** 21E implementation decision

## 1. Purpose

This decision resolves scope authorization against the current Auneron data
model. It does not modify the frozen endpoint, lifecycle, or security
contracts.

Authorization is evaluated in this order:

```text
operation permission
scope capability
target resource authorization
target existence
```

General operation denial is explicit. Missing or inaccessible targets remain
opaque.

## 2. Scope capabilities

The V1 least-privilege assignment is:

| Role | Read global | Manage global | Read another user scope |
|---|---:|---:|---:|
| `viewer` | no | no | no |
| `analyst` | no | no | no |
| `manager` | no | no | no |
| `executive` | no | no | no |
| `administrator` | yes | yes | yes |
| `developer` | yes | yes | yes |

The concrete capabilities are:

```text
memory:read_user_scope
memory:read_global
memory:manage_global
```

Operation and scope permissions are cumulative. Neither replaces the other.

## 3. Account scope

The current Auneron schema has no row-level user-to-account membership.
Memory V1 therefore reuses the existing account resource permissions:

```text
read/history              -> clients.view
create/evidence/lifecycle -> clients.manage
```

The target `account_id` must exist. Absence of the account or absence of its
resource permission produces the same inaccessible-target result.

This is the narrowest policy expressible by the current data model without a
new authorization system or a schema migration. A future user-to-account
membership can further restrict the policy without changing the Memory HTTP
contract.

## 4. User scope

A user can operate on their own user scope when the operation permission is
present.

Reading or requesting history from another user scope additionally requires:

```text
memory:read_user_scope
```

Writing or applying lifecycle operations to another user scope is not part of
V1, including for administrator and developer roles.

## 5. Global scope

Read and history require:

```text
memory:read_global
```

Create, evidence, supersede, invalidate, and archive require:

```text
memory:manage_global
```

## 6. Failure semantics

| Condition | Domain error | Future HTTP mapping |
|---|---|---:|
| Missing operation permission | `MemoryAuthorizationError` | `403` |
| Missing or inaccessible scope target | `MemoryNotFoundError` | `404` |
| Malformed scope | `MemoryValidationError` | `422` |

The policy never exposes whether a protected account, user, or memory exists.

## 7. Invariants

- reuse the existing server-side RBAC;
- do not create a second authorization system;
- do not add a database migration in 21E;
- validate operation permission before scope authorization;
- validate scope before calling `MemoryService`;
- do not use `created_by_user_id` as ownership;
- do not treat administrator or developer as an authorization bypass;
- do not expose protected target existence;
- preserve the frozen Memory API contract.
