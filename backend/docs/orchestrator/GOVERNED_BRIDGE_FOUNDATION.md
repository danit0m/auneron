# Governed Bridge Foundation

## 25F scope

25F introduces the first safe bridge foundation between the quarantined legacy
Orchestrator and the governed Skill catalog. The bridge is intentionally
read-only and advisory.

`OrchestrationDecision.selected_agents` may be projected to existing enabled
`AgentSkillBinding` rows. Only bindings whose `SkillVersion` is published and
whose `SkillDefinition` is active are eligible for the projection.

The projection exposes a bounded metadata allowlist:

- agent name
- binding ID
- SkillVersion ID
- Skill ID
- binding priority
- execution mode
- runtime kind

Binding configuration, handler references, manifests, capabilities, input
payloads, authority identities, Approval state, runtime context and Memory are
not projected.

## Authority boundary

An advisory agent name is not a principal. A binding is not a grant of
authority. A published SkillVersion is not executable intent.

The legacy EventBus path does not carry an authorized `authority_user_id`,
while governed Work/Skill execution requires current authority and independent
authorization. 25F therefore does not create Work, configure or dispatch
WorkSkillExecution, execute a governed Skill, create or consume Approval, or
synthesize a user principal.

## Production wiring

There is no production EventBus wiring in 25F. The projection service is an
internal SELECT-only foundation that can be exercised directly by tests and
future migration design.

A later bridge may consume this projection only after a separate authority
provenance, idempotency, Approval and recovery contract is frozen and gated.

## Explicit non-goals

25F does not:

- call legacy agent handlers;
- invoke `ExecutionPipeline`;
- create or mutate Work;
- execute Skill runtime;
- create or consume Approval;
- mutate Memory;
- modify public API/OpenAPI;
- add a database migration;
- enable automatic action selection, retry or replan.
