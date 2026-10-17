# Budget ledger (INTELLIGENCE-IMPL-0)

Status: foundation, lab only. No real provider, no paid call, no real data.
Source contract: INTELLIGENCE-02A (sections R01, budget formulas and
transitions T1-T8).

## Tables

Only two tables exist in this phase (migration `a7d3e91c4b26`, additive,
`down_revision = 5c1e7a90d2b4`):

| Table | Purpose |
|---|---|
| `intelligence_budgets` | Current balance per budget scope (user/period, objective, deployment/period). |
| `intelligence_model_calls` | One row per provider attempt; each row is in exactly one state. |

## Buckets and invariants

Per budget (separately for money in micro-USD and for tokens):

* `L` limit, `R` reserved (worst-case hold for `reserved` + `in_flight`),
  `S` settled (`min(actual, reserved)`), `U` unknown (held at its worst
  case), `O` overrun (`max(actual - reserved, 0)`).

| ID | Invariant | Enforced by |
|---|---|---|
| BI-1 | `R + S + U <= L`, all buckets `>= 0` | Database CHECK constraints (`ck_intelligence_budgets_*`) |
| BI-2 | Admission: `R + S + U + O + r <= L` on every budget of the call, all-or-nothing, plus `U < theta * L` | One `UPDATE ... WHERE` per budget (repository `try_reserve`), budgets visited in ascending id order, one transaction |
| BI-3 | Conservation: every call sits in exactly one bucket | Transition code + `BudgetLedger.verify_conservation` |
| BI-4 | Confirmed spend is always visible as `S + O` | Settlement splits `actual` into `S` and `O` |
| BI-5 | `U` is never released automatically | No code path releases `unknown` without `evidence_ref` and `reconciled_by_reference` |
| BI-6 | Exposure is at most `L + O` | `O` is counted by admission; see "Residual exposure" |

## Transitions (each one database transaction, committed before returning)

| ID | From -> To | Balance effect | Notes |
|---|---|---|---|
| T1 | none -> `reserved` | `R += r` | Atomic admission (BI-2). |
| T2 | `reserved` -> `in_flight` | none | **Write-ahead mark**: committed (`sent_at`) before any network write. Deadline re-based to `sent_at + timeout`. |
| T3 | `in_flight` -> `settled` | `R -= r; S += min(a, r); O += max(a - r, 0)` | Refusals and truncations are settled (charged). |
| T4 | `in_flight` -> `unknown` | `R -= r; U += r` | Timeout, transport error after send, 400/429/5xx, any unexpected error, crash after send. |
| T5 | `reserved` -> `released` (`never_sent`) | `R -= r` | Crash before the mark; no evidence needed. |
| T5 | `in_flight` -> `released` (`never_sent`) | `R -= r` | Only with adapter-attested not-sent evidence (`evidence_ref` mandatory, also a CHECK). |
| T6 | `in_flight` -> `released` (`provider_documented`) | `R -= r` | Only through `PROVIDER_NO_CHARGE_POLICIES`, which is **empty by default**. |
| T7 | `unknown` -> `settled` | `U -= r; S += min(a, r); O += max(a - r, 0)` | Needs `evidence_ref` and `reconciled_by_reference`. |
| T8 | `unknown` -> `released` (`admin_reconciliation`) | `U -= r` | Needs `evidence_ref` and `reconciled_by_reference`. |

Any other edge is rejected deterministically (`INVALID_TRANSITION`) and a
rejected transition changes nothing (covered by the transition-matrix test).
A repeated transition is rejected, never applied twice. A repeated
`call_key` is rejected (`DUPLICATE_CALL_KEY`).

400, 429, 5xx and timeouts are never presumed free: they end in `unknown`
and hold the worst case until evidence says otherwise.

## Restart recovery (`BudgetLedger.recover`)

* `reserved` past `deadline_at + grace` -> `released(never_sent)`: the
  `in_flight` mark never committed, so nothing was sent.
* `in_flight` past `deadline_at + grace` -> `unknown`: it may have been
  sent. Never released, never settled by guess. A process that died after
  the mark but before the network write is therefore also held as
  `unknown` (conservative by design; reconcile with evidence).
* Idempotent: each row is re-validated under its row lock.
* If the real response arrives after recovery already moved the call to
  `unknown`, the gateway settles it through T7 with
  `evidence_ref = late_response:<provider request id>` and
  `reconciled_by_reference = system:gateway`.

## Audit scope (mandatory caveat)

`intelligence_budget_entries` does **not** exist in this phase.
Therefore this ledger is **NOT append-only** and does **not** provide a full
historical trail.

* What is verifiable today is **current accounting conservation**: the
  balances in `intelligence_budgets` can be recomputed from the rows in
  `intelligence_model_calls` and compared (`verify_conservation`).
* What is **not** available yet is an **immutable transition history**:
  intermediate balances and the exact order of balance changes are not
  stored, and rows in `intelligence_model_calls` are updated in place.

Evidence that does persist per call row: `reserved_at`, `sent_at`,
`unknown_at`, `settled_at`, `released_at`, `reconciled_at`, `attempt` and
`parent_call_id`, `release_basis`, `evidence_ref`,
`reconciled_by_reference`, `error_class`, request/response SHA-256 digests,
`reserved_*` and `actual_*` amounts, and a `version` counter per budget.
Evidence that arrives later with `intelligence_budget_entries`: an immutable,
append-only entry per balance change.

## Residual exposure (BI-6)

The internal ceiling is not an absolute billing cap. A confirmed overrun
(`O`) is recorded explicitly and counted by later admissions, but spend can
exceed `L` by the overrun. Before any paid call, an external hard cap at the
provider and invoice reconciliation are required (future gate).

## Privacy of the ledger

Only SHA-256 digests of requests/responses are stored, never content.
`data_classification` is restricted to `synthetic` and `provider` to `fake`
by CHECK constraints; real data or a real provider require a new migration.
