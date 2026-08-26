# Legacy Orchestrator Observe-Only Quarantine

**Status:** Commit 25E safety boundary.

## Purpose

The legacy autonomy plane predates the governed Work/Skill/Approval runtime.
Its historical production path was:

```text
EventBus
  -> AIOrchestrator.execute
  -> DecisionEngine
  -> AgentRegistry
  -> ExecutionPipeline
  -> agent.handler(payload)
```

Registered legacy handlers can open application database sessions and create
knowledge records, but this path does not carry a current `authority_user_id`
into Work/Skill/Approval authorization. Decision output, selected agent names,
model output, Memory and learning context are data and cannot create authority.

Commit 25E therefore quarantines legacy execution before any governed adapter is
designed.

## Production path after 25E

```text
EventBus.publish
  -> AIOrchestrator.observe
  -> DecisionEngine.decide
  -> existing in-memory DecisionStore
  -> AgentRegistry candidate resolution
  -> STOP
```

`AIOrchestrator.observe()` returns the `OrchestrationDecision`. Candidate agents
remain advisory metadata only. No `ExecutionPipeline`, legacy handler, execution
metric or execution telemetry record is produced by observation.

## Fail-closed compatibility symbols

`AIOrchestrator.execute()`, `ExecutionPipeline.execute()` and
`ExecutionPipeline._execute_agent()` remain import-compatible for legacy code,
but immediately raise `LegacyAutonomyExecutionBlockedError`.

There is no environment, configuration, API or test bypass for this quarantine.

## Preserved components

The following remain unchanged in 25E:

- Decision Engine rules and normalization;
- in-memory DecisionStore;
- AgentRegistry registrations and candidate selection;
- legacy agent modules as migration references;
- public Orchestrator diagnostics, which remain GET-only.

## Governed plane remains separate

25E does not modify or bridge:

- WorkSkillExecution;
- GovernedSkillExecution;
- Work learning context or durable runtime-context snapshots;
- SkillRuntime or isolated worker transport;
- ApprovalService or autonomy policy;
- public Work/Skill request schemas.

No Work is created, no Skill is selected, no approval is bypassed and no
runtime context is treated as authority.

## Deferred governed migration

A future checkpoint may design a Decision-to-Work **proposal** adapter only
after authority provenance, exact Skill mapping, idempotency, approval semantics
and failure recovery are frozen independently. Automatic Work creation, Skill
selection, action selection and retry/replan remain blocked.
