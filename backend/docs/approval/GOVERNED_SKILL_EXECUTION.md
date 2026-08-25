# Governed Skill Execution V1

**Status:** Commit 24D

## 1. Purpose

24D is the internal execution boundary that turns the 24C autonomy decision
into a governed Skill invocation without exposing a new HTTP entry point.

The caller must already have selected one exact published Skill version and
must provide a server-resolved non-human actor plus a current human authority
principal. Work, Memory, model output, bindings and plugin text remain context,
never authority.

## 2. Execution paths

### Low-risk read-only

`agent`, `system` and `integration` actors may execute a policy-approved
`read_only` action without an Approval record. The current authority user is
still re-authorized through the existing Skill RBAC/scope boundary and an
idempotency key is mandatory.

### Mutating and external

High/critical actions require an approved, unexpired ApprovalRequest matching:

- exact non-human actor type/reference;
- exact SkillVersion and current manifest digest;
- exact canonical input digest;
- current policy risk and required approval permission;
- exact account/user scope targets.

Critical execution additionally requires persisted evidence that the sensitive
human decision was made with an elevated session.

## 3. Approval is not RBAC

Before runtime, 24D resolves a current active authority user and calls the
existing Skill authorization boundary. The current authority user must still
hold all Skill execution and resource-scope permissions.

For external execution, durable sensitive human approval satisfies the
interactive sensitive gate, but `skill:execute_external` remains independently
required from the current authority principal.

## 4. One-time consumption and crash-safe replay

`approval_consumptions` reserves one ApprovalRequest/ApprovalDecision exactly
once before runtime. The runtime idempotency key is deterministically derived
as `approval:<request_id>`.

The reservation is committed before entering `SkillRuntimeService`. If a
process stops after the runtime ledger is committed but before consumption is
finalized, a retry reaches the same runtime idempotency identity and can safely
link the original invocation. The handler is not executed a second time.

A runtime failure after an invocation row exists still consumes the approval.
A failure before any invocation ledger exists marks the consumption terminal
`failed`; the approval cannot be reused for a later attempt.

## 5. Autonomous handler trust boundary

24D does not treat runtime allowlisting alone as sufficient authority for
autonomous execution. Every governed non-human execution additionally requires:

- `runtime_kind == internal_python`;
- the exact handler to be present in `SkillHandlerRegistry`;
- `trusted_for_autonomy=True` on that exact registration;
- a server-controlled `autonomy_entrypoint` executed through the isolated process boundary.

The default remains fail-closed (`False`). `plugin` runtime remains blocked from
the autonomous path even if a plugin handler is allowlisted for explicit human
execution. Beginning in 24E.1, governed autonomous handlers execute in a dedicated
child Python process. When the published timeout expires, the process/process-tree
is terminated and waited for before the runtime records `timed_out`. This closes
the Python-thread hard-kill gap for the trusted autonomous path only. It is not a
filesystem, network, syscall or container sandbox and it cannot undo side effects
that completed before termination.

## 6. Explicit exclusions

24D does not:

- add a public HTTP route;
- let a request choose `agent/system/integration` authority;
- select a Skill from model output;
- mutate Work or Memory;
- make AgentSkillBinding authoritative;
- implement Work/Orchestrator scheduling;
- add distributed autonomous rate limiting or stale reservation maintenance.

Those operational integrations and final hardening belong to 24E.

## 24E.2 Work integration boundary

A new internal `WorkSkillExecutionService` may request this governed boundary
for one exact Work action. It does not weaken any 24D/24E.1 gate:

- actor identity is derived server-side as `system:work:<work_item_id>`;
- current authority user, RBAC and Skill scope are rechecked;
- mutating actions still require the exact ApprovalRequest;
- read-only actions retain deterministic runtime idempotency;
- external Work dispatch remains blocked in 24E.2;
- raw input is not persisted in the Work execution ledger;
- the legacy Orchestrator receives no dispatch authority in this checkpoint.

Work status changes continue through `WorkManagerService`, preserving dependency
checks, optimistic versioning and WorkEvent idempotency.
