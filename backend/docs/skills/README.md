# Agent Skills

**Roadmap:** Commit 23

**Status:** Commit 23 complete — Gate 23E hardening finalized

Agent Skills are explicit, versioned capabilities that an Auneron agent may
request through a controlled runtime. They answer:

```text
What capability exists?
Which exact version and handler implement it?
What data contract does it accept and return?
What resources does it declare that it needs?
Which agents are allowed to discover the version?
```

A skill declaration is not permission, approval or authority. Commit 23 builds
the execution capability and audit boundary. Approval and autonomous selection
belong to Commit 24.

## Documents

- `AGENT_SKILLS_ARCHITECTURE.md`: component and trust boundaries.
- `AGENT_SKILLS_DATA_MODEL.md`: tables, constraints, indexes and delete rules.
- `AGENT_SKILLS_THREAT_MODEL.md`: threats, controls and deferred risks.
- `AGENT_SKILLS_SERVICE_CONTRACT.md`: canonical digest, transactions,
  publication, immutability and binding resolution.
- `AGENT_SKILLS_RUNTIME_CONTRACT.md`: allowlist, JSON Schema validation,
  idempotency, execution limits and retained outcomes.
- `AGENT_SKILLS_RUNTIME_CONTEXT.md`: 25C side-band learning-context protocol,
  dual opt-in, safe metadata and identity preservation.
- `AGENT_SKILLS_AUTHORIZATION.md`: RBAC, resource scope, anti-IDOR and
  explicit-execution HTTP boundary.
- `AGENT_SKILLS_OPERATIONS.md`: runtime telemetry, rate limits, stale recovery
  and production constraints.

## Commit 23 gates

```text
23A  catalog, versioned manifests, capabilities and agent bindings
23B  repository, transactional service and immutable publication
23C  invocation runtime, idempotency, limits and execution history
23D  RBAC, scope authorization and explicit-execution API
23E  observability, hardening, documentation and final commit
```

Gate 23E finalizes runtime observability, per-user abuse limiting, stale
invocation recovery and the production operating contract. The cumulative
Commit 23 audit must pass before staging or commit. After that audit, Commit 23
is pushed to `origin/main` and the working tree must be clean.

## Commit 25C runtime-context foundation

25C introduces the internal `work_learning_v1` side-band protocol for future
authorized Work learning context. The first APPLY is protocol-only: it keeps
`input_payload`, Approval identity, public API, Work execution services,
database schema and the legacy Orchestrator unchanged. Contextful execution is
restricted to trusted `internal_python`, `read_only`, isolated handlers with
manifest + registry dual opt-in.
