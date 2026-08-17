# Work Manager Recurrence Contract V1

**Status:** Frozen for Commit 22

## 1. Schedule and SLA

All accepted datetimes must include timezone information and are normalized to
UTC. `sla_due_at` cannot follow `due_at` when both exist. Schedule changes are
versioned mutations and emit `schedule_changed`; a no-op is rejected.

SLA evaluation is read-only:

| Result | Meaning |
|---|---|
| `not_configured` | no SLA deadline |
| `on_track` | open and not past the SLA instant |
| `breached` | open and past the SLA instant |
| `met` | completed no later than the SLA instant |
| `missed` | completed after the SLA instant or without a valid completion time |
| `cancelled` | terminal cancellation |

Breach queries exclude terminal items, order oldest deadline first and accept
at most 100 rows.

## 2. Rule configuration

One template may own one rule. The template requires a stable work key short
enough for the deterministic `:occ:<number>` suffix. Frequencies are `daily`,
`weekly` and `monthly`; interval is 1 through 365. Timezone names must resolve
through the IANA database.

Optional end and maximum-occurrence boundaries stop future generation.
Optional `sla_lead_minutes` derives each generated SLA by subtracting the lead
from its scheduled deadline.

Daily and weekly advances preserve configured local wall-clock time across
timezone offset changes. Monthly advances preserve the local day when
available and clip it to the destination month's final day otherwise.

## 3. Explicit generation

22C does not run a background scheduler. A caller lists bounded due rules and
explicitly requests the next occurrence.

Generation locks the template and rule, verifies that the rule is active and
due, and atomically creates:

1. a backlog work item keyed `<template-key>:occ:<number>`;
2. its `created` event;
3. the unique occurrence identity;
4. the template `recurrence_generated` event and version increment;
5. the next rule instant, or the inactive terminal rule state.

The generated item copies the template's type, title, description, scope,
parent, assignee, priority and context. Its due instant is the scheduled
occurrence. Origin is the recurrence rule and occurrence number.

## 4. Retry and failure behavior

Generation accepts the template's expected version and an optional event
idempotency key. An equivalent retry returns the original generated item and
occurrence. Different concurrent requests with one expected version produce
one success and one version conflict.

Database uniqueness independently protects rule/number, rule/scheduled instant,
generated item and scoped work key. Any exception rolls back the item, both
events, occurrence row, rule advance and template version together.
