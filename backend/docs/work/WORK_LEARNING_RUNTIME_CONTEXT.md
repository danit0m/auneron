# Work Learning Runtime Context

**Status:** 25D production Work learning hook.

## Purpose

25D connects the authorized deterministic outcome metadata introduced in 25B
to the side-band Skill Runtime protocol introduced in 25C. The connection is
internal and applies only to governed `WorkSkillExecution` actions that are
`internal_python`, `read_only`, isolated, and explicitly opted in through both
the immutable SkillVersion manifest and the trusted server handler registry.

Learning context remains data. It never grants Skill authority, Work scope,
Memory scope, role, Approval authority, or permission to select another Skill.

## Immutable snapshot

A context-enabled Work execution owns at most one
`WorkLearningRuntimeContextSnapshot`.

The snapshot stores only:

- the exact `work_skill_execution_id`, target `work_item_id` and
  `skill_version_id`;
- protocol `work_learning_v1`;
- the normalized protocol payload;
- its SHA-256 canonical digest;
- item count and canonical byte count;
- the server UTC `resolved_as_of` instant;
- creation timestamp.

The production resolver requests at most five outcome items. The protocol
ceiling remains ten items and 16384 canonical bytes.

The stored item allowlist is exactly:

```text
memory_id
source_work_item_id
work_skill_execution_id
skill_version_id
terminal_status
evaluation_code
learning_signal
observed_at
```

No raw Skill input/output/error, Memory title/content/context/evidence,
credentials, Approval payload, actor reference, role, or scope authority is
persisted in the snapshot.

## Authorization and retry behavior

On every snapshot access the service reloads the current persisted target Work
and current persisted authority User and independently requires current Work
`read` and Memory `read` authorization for the exact Work scope.

On first use, the service resolves 25B metadata at one server-selected UTC
`as_of`, normalizes it through the 25C runtime-context validator, and persists
the immutable snapshot. An empty authorized result is represented explicitly
as `items=[]`.

On retries the service re-authorizes current Work and Memory reads but does not
re-query prior outcomes. The exact stored payload and digest are reused. This
prevents context drift under the Work dispatch idempotency key and the 25C
runtime request fingerprint.

Snapshot binding, protocol, digest, item count and canonical byte count are
revalidated before reuse. Any mismatch fails closed. There is no automatic
snapshot refresh or contextless fallback for an opted-in execution.

## Dispatch ordering

For a context-enabled read-only Work execution:

1. existing SkillInvocation reconciliation remains first;
2. current Skill execution authority is revalidated;
3. the Work dispatch rate limit is consumed;
4. current Work and Memory read authority is checked;
5. the immutable snapshot is loaded or created;
6. after a creating commit, Work/Execution locks and current Skill authority are
   reacquired;
7. only then may Work transition to `in_progress`;
8. the dispatch attempt is persisted;
9. Governed execution receives the normalized side-band context;
10. Skill Runtime independently revalidates protocol, manifest, registry,
    runtime kind, execution mode and isolation before the handler receives
    `handler(payload, runtime_context)`.

If context resolution or snapshot creation fails, Work is not started,
`dispatch_attempts` is not incremented, and no handler is invoked.

## Identity boundary

25D does not change:

- `input_payload`;
- Skill input schema;
- Work `input_digest`;
- Approval input identity or Approval request fingerprint;
- Work dispatch key;
- contextless Skill Runtime fingerprint;
- public Skill or Work API schemas.

The contextful request fingerprint remains the 25C binding to
`runtime_context.protocol` plus the immutable snapshot digest.

## Forbidden paths

25D does not enable learning context for mutating or external Skills, does not
expose runtime context through HTTP, does not alter the legacy Orchestrator,
and does not add automatic action selection, retry, replan, or model-driven
authority.
