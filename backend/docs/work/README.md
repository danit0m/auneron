# Work Manager

**Roadmap:** Commit 22
**Status:** Commit 22 final candidate

The Work Manager answers:

```text
What needs to be done?
Who is responsible?
What blocks execution?
When is it due?
What changed and why?
```

It does not decide whether a sensitive action is authorized. Approval and
autonomy policy belong to Commit 24.

## Documents

- `WORK_MANAGER_ARCHITECTURE.md`: boundaries and aggregate rules.
- `WORK_MANAGER_DATA_MODEL.md`: tables, constraints, indexes and delete rules.
- `WORK_MANAGER_THREAT_MODEL.md`: threats, controls and deferred risks.
- `WORK_MANAGER_SERVICE_CONTRACT.md`: repository, transaction, concurrency and
  idempotency contracts.
- `WORK_MANAGER_LIFECYCLE_CONTRACT.md`: exact transition and dependency rules.
- `WORK_MANAGER_RECURRENCE_CONTRACT.md`: persisted recurrence and generation
  rules.
- `WORK_MANAGER_AUTHORIZATION.md`: RBAC, scope, actor and HTTP security
  contract.
- `WORK_MANAGER_OPERATIONS.md`: structured logs, paging, monitoring and
  release/rollback runbook.

## Commit 22 gates

```text
22A  domain, schema, migration and database invariants
22B  repository, service contracts and transactional operations
22C  lifecycle, dependencies, scheduling, SLA and recurrence
22D  RBAC, scope authorization and secure API
22E  observability, hardening, documentation and final commit
```

Every gate remains uncommitted until the cumulative Commit 22 audit passes.
The final Commit 22 must be registered in Git, pushed to `origin/main`, and
leave the working tree clean.

## Implemented in Commit 22

- aggregate schema and PostgreSQL invariants;
- repository without transaction control;
- append-only event repository contract;
- transactional creation, detail, priority and assignment operations;
- transactional comments and system notes;
- row locking plus optimistic `expected_version` validation;
- replay-safe event idempotency;
- actor and JSON payload validation;
- rollback of current state when event persistence fails.
- explicit lifecycle matrix and immutable terminal business state;
- acyclic, same-scope dependency graph with serialized graph mutation;
- dependency gates for start and completion;
- timezone-aware schedule changes and side-effect-free SLA evaluation;
- persisted daily, weekly and monthly recurrence rules;
- atomic, idempotent generation of deterministic occurrence items;
- least-privilege Work Manager permissions by operation;
- global, account and user scope authorization with opaque resource denial;
- assignment authorization distinct from work scope;
- authenticated-user actor and API origin binding;
- secure request/response schemas and Work Manager HTTP routes;
- authorized Memory links, optimistic-conflict mapping and idempotency headers;
- bounded payloads, sanitized errors and protected-response `no-store`.
- bounded cursor pages for historical collections;
- safe domain-change telemetry correlated by request ID;
- operational monitoring, release and rollback procedures.

Commit 22 introduces no scheduler process or external side effect. Callers
explicitly request generation for due recurrence rules. The Work Manager can
organize and audit work, but it cannot authorize or execute a sensitive action.
That authority remains reserved for the policy and approval boundary in
Commit 24.

## Commit 25B authorized learning context

25B adds an internal read-only `WorkLearningContextService` that can resolve
prior deterministic 25A outcomes for the exact persisted Work scope and same
Skill version. Current Work-read and Memory-read authorization are rechecked
from the persisted authority User before any candidate query.

The resolver returns safe outcome metadata only and is not connected to Skill
runtime in the first APPLY. It does not alter `input_payload`, handler
signatures, OpenAPI, Work execution, approval/autonomy policy or the legacy
Orchestrator. Learning and Memory remain context, never authority. See
`WORK_LEARNING_CONTEXT.md`.

## Commit 25D durable Work learning runtime context

25D connects the authorized 25B outcome resolver to the 25C side-band runtime
protocol for explicitly opted-in, isolated `internal_python` `read_only` Work
Skills.

The first authorized context is persisted as one immutable snapshot per
`WorkSkillExecution`; retries re-authorize Work and Memory reads but reuse the
same snapshot and digest. Production retrieval is bounded to five items.

The hook does not modify `input_payload`, Approval identity, Work dispatch
identity, public APIs or the legacy Orchestrator. Mutating/external context,
automatic action selection and automatic retry/replan remain forbidden.

See `WORK_LEARNING_RUNTIME_CONTEXT.md`.
