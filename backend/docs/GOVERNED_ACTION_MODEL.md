# Governed Action Model V1

## 1. Purpose and Boundary

The Governed Action Model V1 (GAM V1) is the conceptual contract that any
mutable governed action in Auneron must satisfy, or explicitly declare as a
known deviation. It is not a code architecture: it introduces no classes,
interfaces, ORM models or API contracts, and it does not correct or migrate
any existing corridor.

**Boundary:** this document defines what must be true about a governed
action, not how that is implemented. It is deliberately cross-cutting — it
does not belong to the `approval` or `work` domain specifically, and it does
not privilege either domain's mechanisms as universal.

## 2. Evidence Basis

GAM V1 was extracted from the mechanical comparison of the two mutable
action corridors Auneron has proven operationally to date:

- `account.mark_overdue` — the `WorkItem`-oriented human corridor
  (`HumanAccountMarkOverdueMaterializationService` /
  `HumanAccountMarkOverdueExecutionService`), proven across three
  operational episodes (`CONTROLLED_PILOT_EVIDENCE.md`,
  `CONTROLLED_OPERATOR_UI_FLOW_EVIDENCE.md`,
  `CONTROLLED_REPEATABILITY_PILOT_EVIDENCE.md`).
- `account.mark_paid` — the `ApprovalRequest`-oriented corridor
  (`AccountMarkPaidExecutionService`, ADR 009 in code comments), proven by
  the Second Mutating Skill — Safety Delta V1 functional suite and one
  operational episode (`CONTROLLED_MARK_PAID_PILOT_EVIDENCE.md`).

N3 additionally rests on two concurrent-execution reproducers —
`tests/test_human_account_mark_overdue_execution_concurrency.py` and
`tests/test_account_mark_paid_execution_concurrency.py` — each exercising
two competing authorities against the same governed business condition
from independent sessions, with genuine database-level blocking observed
before release, verifying a single/compatible business effect.

This document does not reproduce those evidence packages. It records only
the properties that survived a Discovery / Reconciliation / Normative
Boundary Decision process comparing the two corridors field by field —
never a property inferred from a single corridor alone.

## 3. Formal Classification

```
OBSERVED INVARIANT
  A property descriptively proven — operationally or functionally — in the
  corridors examined today. Not, by itself, a requirement for the future;
  a fact about the present.

DESIRED INVARIANT
  A normative guarantee GAM V1 requires of any mutable governed action.
  May not be satisfied today by every corridor — it remains a requirement
  regardless.

IMPLEMENTATION MECHANISM
  A concrete, already-existing way of realizing a guarantee (Observed or
  Desired). Never part of the universal contract by itself — two different
  mechanisms may satisfy the same invariant.

KNOWN DEVIATION
  An existing corridor that does not satisfy a Desired Invariant. Part of
  the conformance model, not a blocker that must be eliminated for the
  model to exist.

UNPROVEN PROPERTY
  A hypothesis for which current evidence — two corridors, one with a
  single operational episode — does not support either obligation or
  generalization.
```

## 4. Normative Invariants

### N0 — Separation of Request and Decision Authority
> For a governed high-risk action, the actor requesting authority must
> differ from the actor approving that authority.

- **Requires:** the requester and approver identities to be distinct for
  high-risk/critical actions.
- **Does not require:** any cardinality beyond that distinction.

### N1 — Separation of Decision and Execution Authority
> For a high-risk governed action, the actor exercising execution authority
> must differ from the actor who made the approval decision.

- **Requires:** approver and executor identities to be distinct for
  high-risk/critical actions.
- **Does not require:** that the executor differ from the requester. The
  existing corridors do not support a universal `Requester != Executor`
  rule; that relation stays outside N1 and is classified as UP-1 (§9).

### N2 — Governed Pre-Effect Failure Contract
> Expected failures occurring before the business effect must remain
> governed and typed and must not escape as unclassified internal
> failures.

- **Requires:** expected domain/authorization conditions before the effect
  (invalid state, insufficient authority, mismatched identity, missing
  scope/resource) to produce a classified, observable response.
- **Does not require:** any specific exception hierarchy, any fixed set of
  classes (`ApprovalError`+`SkillError` is a mechanism, not the
  invariant), any specific HTTP status code.

### N3 — Single / Compatible Business Effect
> Concurrent or competing authorities must not produce duplicate or
> incompatible business effects for the same governed business condition.

- **Requires:** that once a concurrent authority has produced the effect,
  no other attempt (same or distinct authority) produces a second,
  duplicate or incompatible effect.
- **Does not require:** convergence of authority at creation/approval time
  (that is `DEFERRED-CONCURRENT-AUTHORITY`, a separate property — §9). Does
  not require any specific mechanism (`WorkItem`, receipt, uniqueness of
  `ApprovalConsumption` are implementations, not the invariant).

```
Requester ──N0──≠── Approver ──N1──≠── Executor
```
This explicitly does **not** imply `Requester != Executor`.

## 5. Conceptual Lifecycle

```
Business condition
       ↓
Governed request
       ↓
Decision authority
       ↓
Execution authority
       ↓
Pre-effect validation
       ↓
Business effect
       ↓
Persistent proof
```
None of these labels implies a mandatory class, table or service. They
describe the conceptual sequence shared by the examined corridors without
promoting recovery/reconstructability to a GAM V1 normative requirement.
Recovery remains an additional non-normative evidence expectation discussed
in §12.

## 6. Current Corridor Conformance

| Desired invariant | `mark_overdue` | `mark_paid` |
|---|---|---|
| N0 — requester ≠ approver | compliant | compliant |
| N1 — approver ≠ executor | compliant | compliant |
| N2 — governed pre-effect errors | compliant | compliant |
| N3 — no duplicate/incompatible effect | compliant | compliant |
| **Aggregate state** | **GAM V1 COMPLIANT** | **GAM V1 COMPLIANT** |

This conformance applies exclusively to the two corridors formally
assessed against GAM V1 — `account.mark_overdue` and `account.mark_paid`
— and must not be read as a claim about any other mutable action in
Auneron. It must not be reinterpreted later as an exhaustive proof of
every possible concurrent scenario — it reflects the specific episodes,
functional tests and concurrent reproducers exercised to date (§2, §8).
`DEFERRED-CONCURRENT-AUTHORITY` (UP-2, §9) remains a separate, still
deferred property: N3 proves competing authorities do not produce a
duplicate effect once execution is reached, not that authority creation
itself converges.

## 7. Current Implementation Mapping — Non-Normative

```
N0/N1 mechanism today:   ApprovalService.decide() — requester != decider SoD
                          for risk high/critical; N1 via a structural guard
                          exclusive to mark_paid (authority.id ==
                          decision.decided_by_user_id)
N2 mechanism today:      ApprovalError/SkillError hierarchies mapped per
                          route — complete in mark_overdue, partial in
                          mark_paid
N3 mechanism today:      mark_overdue = uq_work_items_account_key +
                          WorkEvent.idempotency_key (receipt);
                          mark_paid = ApprovalConsumption.approval_request_id
                          (unique) + expected_status revalidation
Pre-effect authority:    authorize_skill_execution() — invoked at different
                          points of the lifecycle in each corridor
Concurrency:             pessimistic locks in both; WorkItem.version
                          (optimistic) only in mark_overdue
Persisted evidence:      ApprovalConsumption + SkillInvocation + AccountEvent
                          (both); + WorkEvent (mark_overdue only)
Recovery:                backup_postgres.py / restore_postgres.py,
                          auneron_recovery_drill (both, same tooling)
```
None of the above is part of the universal contract — a different corridor
may satisfy N0–N3 through entirely different mechanisms.

## 8. Known Deviations (Historical — All Closed)

```
KD-1
  N1 / account.mark_overdue
  CLOSED @ 4629bb45d487e988d81688c64077243b09cdce77
  account.mark_overdue did not structurally enforce N1. Fixed by an
  explicit authority.id == decision.decided_by_user_id guard before
  the business effect.

KD-2 / FINDING-MARK-PAID-001
  N2 / account.mark_paid
  CLOSED @ 8593ed6d499b569f5365bf4c36386b3abf2e1b80
  account.mark_paid did not fully satisfy N2 on the examined surface.
  Fixed by mapping the previously-unclassified failure at the route
  boundary.

FINDING-MARK-OVERDUE-CONCURRENCY-001
  N3 / account.mark_overdue concurrency
  CLOSED @ f060d972bdfcaad2516f53d6082f508120b37343
  Cause: inconsistent retained lock ordering between WorkItem/
  ApprovalRequest and Account under concurrent execution.
  Resolution: non-mutating initial reads no longer retain those row
  locks; the business effect remains serialized by Account; WorkItem
  mutation retains its own internal concurrency control.
```
These deviations are preserved here as history, not corrected
retroactively — each was true at the baseline it names. No deviation
remains open as of `f060d972bdfcaad2516f53d6082f508120b37343`.

## 9. Unproven Properties

```
UP-1  Requester != Executor as a universal requirement — evidence
      insufficient for normative promotion.
UP-2  Authority convergence at request/approval creation as a V1
      requirement (DEFERRED-CONCURRENT-AUTHORITY — remains a property
      separate from N3).
UP-3  payment_observed / any system-observed evidence — both corridors
      are Level 1, Human-Asserted.
UP-4  Generalization to mutable actions not yet implemented.
```

## 10. Deliberately Non-Promoted Mechanisms/Properties

```
WorkItem, WorkEvent, ApprovalRequest, ApprovalConsumption,
SkillInvocation, AccountEvent — mappings of current implementation, not
universal model elements.

Locks (pessimistic/optimistic), receipts, authorize_skill_execution() as a
concrete function — implementation mechanisms, not the contract itself.

Recovery/reconstructability — kept out of the normative contract in V1
(see §12).
```

## 11. GAM V1 Conformance Criteria

A new mutable governed corridor may claim conformance with GAM V1 if, and
only if, it can demonstrate — by operational or functional evidence, not
design intent alone:

```
1. N0 satisfied: requester != approver is structurally prevented from
   coinciding, not merely an operational convention.
2. N1 satisfied: approver != executor is structurally prevented from
   coinciding, for any action classified as high-risk/critical.
3. N2 satisfied: every expected pre-effect failure (state, authorization,
   scope, identity) produces a classified response — no unmapped
   exception reaches the caller as a generic internal error.
4. N3 satisfied: there is proof that a second concurrent attempt, after a
   successful effect, does not produce a second effect.
5. Any deviation from N0–N3 is explicitly declared as a Known Deviation,
   never silently omitted. A corridor with a declared deviation is
   `GAM V1 ASSESSED WITH KNOWN DEVIATION`, never `GAM V1 COMPLIANT`. Only
   full satisfaction of N0–N3 yields `GAM V1 COMPLIANT`.
```

```
GAM V1 COMPLIANT
  Satisfies N0-N3 in full.

GAM V1 ASSESSED WITH KNOWN DEVIATION
  Evaluated against GAM V1; violates one or more Desired Invariants; the
  deviations are explicitly registered.
```

No criterion requires a specific class/table name.

## 12. Additional Non-Normative Evidence Expectation

```
Persistent effect evidence should be independently inspectable from
persisted state, without relying solely on the HTTP response.
```
This is an expectation, not a GAM V1 requirement. Recovery/reconstructability
remains a candidate for a future invariant, not adopted in V1.

## 13. Open Questions / Future Versions

```
Q1  Should N0/N1 also apply to medium/low-risk actions, or remain
    restricted to high/critical?
Q2  Should authority convergence (UP-2) eventually become an N4?
Q3  Should the recovery expectation (§12) be formalized, and with what
    strength — is "reconstructible" sufficient, or does it need a
    time/RPO guarantee?
Q4  Requester != Executor (UP-1): under what domain conditions, if any,
    should this become required?
Q5  How should GAM V1 treat agent-only (non-human) corridors — neither
    proven corridor today is purely agent-only; N0/N1 presuppose distinct
    human actors.
```
