# Agent Skills Architecture V1

**Status:** Finalized through Commit 23E

## 1. Purpose

The existing `AgentRegistry` maps events to Python callables. It does not
provide durable identity, manifest versioning, input/output contracts,
capability declarations or an execution audit trail. Commit 23 adds that
boundary without silently changing legacy agent behavior.

The target flow is:

```text
explicit caller
  -> authorization and scope policy
  -> skill catalog resolution
  -> immutable published version
  -> bounded invocation runtime
  -> result and append-only execution history
```

23D now implements the durable catalog, bounded runtime and authenticated explicit-execution authorization boundary.

## 2. Components

### Skill catalog

`SkillDefinition` owns stable business identity. `skill_key` is globally
unique, canonical and independent from a Python function or plugin location.
Disabling or retiring the catalog entry does not erase its history.

### Versioned manifest

`SkillVersion` pins the runtime kind, handler reference, execution mode,
resource limits, JSON contracts and SHA-256 manifest digest. A version moves
through `draft -> published -> retired`; 23B enforces publication and
immutability transactionally through `SkillService`.

### Capability declaration

`SkillCapability` declares the least set of resources requested by one exact
version. A declaration is descriptive input to policy. It never grants access
and cannot override RBAC, tenant scope or approval requirements.

### Agent binding

`AgentSkillBinding` connects an existing agent name to one exact version. The
binding supports deterministic priority, enablement and non-secret runtime
configuration. It is discovery metadata, not an instruction to execute.

## 3. Trust boundary

The following data is untrusted until independently validated:

- manifest JSON and JSON Schema documents;
- handler references and plugin identifiers;
- binding configuration;
- capability keys supplied by publishers;
- future invocation input and output.

No manifest text, Memory content, Work description or agent response grants
authority. The runtime must receive an independently authenticated actor and
an authorization decision.

## 4. Version resolution

Future resolution must return an exact published `SkillVersion`, never a
mutable alias. Bindings store `skill_version_id`, so an agent cannot change
behavior merely because another version is published. An explicit binding
mutation is required.

`manifest_digest` supplies content identity. 23B calculates the digest from a
canonical envelope containing the complete executable contract and exposes no
client-selected digest input.

## 5. Transaction ownership

`SkillRepository` executes database statements and `flush`, but does not call
`commit`, `rollback`, `begin` or `begin_nested`. `SkillService` owns the
transaction that publishes a version and its capability declarations or
changes a binding.

## 6. Compatibility

Commit 23 does not remove `AgentRegistry`, `EventBus` or the existing agents.
Legacy execution continues while the skill runtime is introduced behind a
separate service boundary. Migration of individual agents can therefore be
incremental and reversible.

## 7. Gate 23C runtime

`SkillRuntimeService` executes one exact published version behind a separate
internal boundary. Persisted handler references never trigger arbitrary import:
`SkillHandlerRegistry` is an explicit allowlist keyed by runtime kind and exact
handler reference.

The runtime canonicalizes JSON input, validates Draft 2020-12 schemas, rejects
remote `$ref`, persists an idempotent invocation ledger, executes through a
bounded worker pool, applies the published timeout and output ceiling, validates
the output contract and stores only sanitized terminal error codes.

`skill_invocations` retains execution history independently from binding or
catalog lifecycle. Raw input and raw exception text are not stored.

The runtime remains a mechanism, not an authority boundary. 23D must resolve
the authenticated caller, RBAC and resource scope before exposing execution.

## 8. Gate 23D authorization and explicit execution

23D exposes exactly one user-initiated execution boundary:
`POST /agent-skills/versions/{version_id}/invoke`.

The service API key and authenticated user session remain independent
prerequisites. The HTTP caller cannot choose runtime actor fields; the route
derives `actor_type=user`, `actor_reference=user:{id}` and `actor_user_id`
from the authenticated session.

Central RBAC distinguishes read-only, mutating and external execution.
Account and user capabilities are authorized against the same reserved
`account_id` and `subject_user_id` fields that are delivered in the
`input_payload` to the runtime. This binding prevents a caller from authorizing
one resource identifier while asking the handler to act on another.

Capability rows remain declarations, not grants. Every declared capability is
considered by policy, including rows marked `required=false`, because Commit 23
does not yet implement per-invocation capability attenuation.

External execution requires both the dedicated external-execution permission
and a currently elevated user session. This is a recent-authentication control,
not the approval workflow reserved for Commit 24.

## 9. Gate 23E operations and recovery

23E closes the Commit 23 runtime operating boundary:

- Skill runtime lifecycle events are structured and request-correlated when an
  HTTP request context exists;
- telemetry contains identifiers and bounded status metadata, never raw input,
  idempotency keys, credentials or raw exception text;
- the explicit Skill API has a per-user sliding-window limiter in addition to
  the bounded executor;
- stale `running` rows are recovered in locked batches using
  `FOR UPDATE SKIP LOCKED`;
- recovery terminalizes the ledger only and never re-executes a handler;
- a background maintenance loop follows the same lifecycle pattern already
  used by authentication maintenance.

## 10. Explicit exclusions after Commit 23

Commit 23 intentionally still contains:

- no autonomous Agent Skills execution;
- no client-selected `agent`, `system` or `integration` actor;
- no public invocation-history endpoint;
- no secret store;
- no sensitive-action approval record;
- no arbitrary dynamic import from catalog text;
- no safe hard-kill of a Python thread after it has started;
- no distributed cross-replica Skill rate limiter.

A timeout stops waiting and records `timed_out`; its bounded executor slot
remains occupied until the callable exits. This is containment, not
cancellation. Commit 23 permits only trusted explicitly allowlisted in-process
handlers. Untrusted executable plugins require a separate process/container
boundary before production enablement.

Sensitive-action approval, autonomous selection and non-user execution
authority belong to Commit 24.
