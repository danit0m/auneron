# 25O Authenticated Advisory Proposal Work Materialization

The pilot implements exactly one mutable internal business action: `account.mark_overdue`.

The only allowed corridor is Proposal -> existing 25M ApprovalRequest -> account-scoped WorkItem -> WorkSkillExecution -> validation-only governed authorization -> dedicated transactional Account effect.

A second Work Approval is forbidden. Generic Work dispatch, generic governed execute, isolated SkillRuntime, plugin runtime and external execution are not used for the business effect.

The critical database commit atomically contains Account status, SkillInvocation, ApprovalConsumption, WorkSkillExecution and an idempotent WorkEvent receipt. No schema or Alembic migration is introduced.
