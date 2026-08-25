# Autonomous Skill Process Isolation

**Status:** Commit 24E.1 design and implementation boundary.

## 1. Purpose

24E.1 closes the Python-thread timeout gap for the governed non-human Skill path
before Work/Orchestrator can obtain autonomous execution capability.

The explicit human Skill API remains on the existing bounded thread executor.
The governed autonomous path uses a dedicated child Python process for each
execution and waits for that process to terminate before a timeout is persisted.

## 2. Eligibility

Process isolation does not grant authority. Before the worker starts, the existing
24C/24D controls still require:

- non-human `agent`, `system` or `integration` actor identity supplied by trusted server code;
- exact active Skill and published SkillVersion;
- current authority user, RBAC and scope reauthorization;
- deterministic autonomy policy result;
- exact valid one-time human Approval for high/critical actions;
- `runtime_kind == internal_python`;
- exact allowlisted handler registration;
- `trusted_for_autonomy=True`;
- a server-controlled `autonomy_entrypoint` on that registration.

Work, Memory, binding configuration, model output, confidence and plugin content
remain non-authority inputs.

## 3. Worker protocol

`IsolatedSkillExecutor` launches:

```text
python app/services/skill_process_worker.py <module:function> <max_output_bytes>
```

Canonical JSON input is sent over stdin. The worker returns one JSON envelope on
stdout. Python stdout/stderr emitted during handler import/execution is captured
inside the worker and never becomes the protocol or application response.
Application secrets such as API/database credentials are intentionally not copied
into the child environment.

The worker entrypoint is independent from persisted catalog text. The catalog
`handler_reference` must still match an explicit in-process registry entry; only
server configuration can attach the additional autonomous process entrypoint.

## 4. Timeout termination

On timeout the parent terminates the worker tree and waits for process death before
raising `SkillExecutionTimeoutError`:

- Windows: `taskkill /PID <pid> /T /F`, with direct process kill as fallback;
- POSIX: the worker starts a new session and the process group receives `SIGKILL`.

The autonomous invocation is then persisted as `timed_out` using the isolated
runtime error code. A timed-out child is not left running in the background.

## 5. Security boundary

This is a **killable process boundary**, not a general untrusted-code sandbox.
24E.1 does not provide filesystem, network, syscall, kernel, namespace or container
isolation. It also cannot undo an external side effect that completed before the
process was killed.

Therefore:

- plugin/untrusted autonomous execution remains blocked;
- external autonomous effects still require sensitive human Approval and handler
  idempotency/reconciliation controls;
- Production Pilot still requires a stronger sandbox/container decision before
  untrusted executable handlers can be enabled.

## 6. Crash and retry semantics

The existing SkillInvocation ledger remains the source of execution state and the
ApprovalConsumption ledger remains one-use. 24E.1 does not add a new database
migration. A worker failure is terminal for that runtime invocation; replay uses
the existing idempotency ledger and does not silently launch a second execution.

A process crash after an irreversible side effect can still leave outcome
ambiguity. Automatic retry of external side effects is therefore not authorized
by this checkpoint.

## 7. Work/Orchestrator boundary

24E.1 intentionally does **not** modify Work Manager or Orchestrator routes/services.
They remain unable to invoke `GovernedSkillExecutionService`. Integration is a
separate 24E.2 checkpoint after the 24E.1 Gate proves process termination,
secret isolation, replay behavior and preservation of the public API boundary.
