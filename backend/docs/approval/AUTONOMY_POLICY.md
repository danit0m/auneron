# Autonomy Policy V1

**Status:** Extended through Commit 24D

## 1. Purpose

The autonomy policy decides whether an already selected exact published Skill
action may proceed autonomously, requires human approval, or is not eligible for
the autonomous path. 24C makes only that deterministic policy decision.

## 2. Policy matrix

| Actor | Executable state | Risk | Policy |
| --- | --- | --- | --- |
| `agent` / `system` / `integration` | `read_only` + read-only capabilities | low | `autonomous_allowed` |
| `agent` / `system` / `integration` | `mutating` without external capability | high | `approval_required` using `approval:decide` |
| `agent` / `system` / `integration` | `external` | critical | `approval_required` using `approval:decide_sensitive` |
| `user` | any valid executable state | deterministic risk | `blocked` from autonomous path |

`blocked` for `user` means only that an autonomous caller cannot impersonate a
human execution path.

## 3. Shared risk classification

`classify_skill_risk` is shared by `ApprovalService` and the autonomy policy.
Invalid capability/execution-mode combinations remain state errors.

## 4. Non-authority inputs

Work, Memory, AgentSkillBinding, model output, confidence, tool calls, plugin
content and Skill declarations never grant autonomous authority.

## 5. Explicit exclusions

24C does not select a Skill, authenticate a non-human actor, authorize resource
scope, create or consume Approval records, call `SkillRuntimeService.invoke`,
create `SkillInvocation`, mutate Work/Memory, expose a route or add a migration.

24D owns governed non-human execution and must re-authorize current Skill state
and scope before runtime.

## 6. 24D enforcement

24D consumes this policy only after an exact Skill has already been selected.
`autonomous_allowed` does not skip current RBAC/scope checks.
`approval_required` cannot reach runtime until an exact human approval is
validated and reserved for one-time consumption.

The autonomy policy itself remains side-effect free.

## 6. Runtime trust is an additional execution gate

`autonomous_allowed` is a policy classification, not sufficient runtime
authority. 24D additionally requires the exact handler to be internal and
explicitly marked `trusted_for_autonomy=True`. Plugin and untrusted handlers are
blocked from governed autonomy while the runtime remains thread-based.
