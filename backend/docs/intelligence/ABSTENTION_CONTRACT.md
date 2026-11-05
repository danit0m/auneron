# Abstention contract `abstention.v1` (phase 1, tests only)

Status: defined and tested only under `backend/tests/`. Not in `plan.v1`, not
in `app/`, not wired to the pipeline, scorer or adapter. No model, Gateway,
database or network is used; answers are scripted.

Files: `tests/intelligence_abstention_contract.py` (contract, rule check,
comparison, gated flow) and `tests/test_intelligence_abstention_contract.py`.

## Schema (closed, flat, no union)

Keys: `contract` (`abstention.v1`), `synthetic_only` (`true`),
`scenario_set_version`, `scenario_id`, `registry_version`, `objective_ref`,
`plan_version` (int, `1`), `decision` (`proceed` | `abstain`).
`abstain_code` is required with `abstain` (one of `ABSTAIN_CODES`) and forbidden
with `proceed`. Any extra or missing key, wrong type or wrong binding is
`invalid_response`.

`ABSTENTION_SCHEMA` (in the contract module) is the same contract as a closed
JSON Schema, Draft 2020-12 (`additionalProperties: false`, no `oneOf`/`anyOf`;
`abstain_code` required by `if/then` only when `decision` is `abstain`, and
forbidden otherwise). The tests check it with `Draft202012Validator.check_schema`
and assert that schema and `parse_response` agree on the whole 12-scenario
matrix and on the explicit list of malformed cases in the test file (that list
only, not every possible input). Divergences are allowed only where a cause is
declared, and each declared case is repaired on that single field to prove that
no second defect hides behind it.

Known differences between schema and parser (the parser is stricter and
decides; the schema is never stricter):

1. Case binding, below.
2. Strict integer type: JSON Schema treats `1.0` as equal to `1`, so
   `plan_version: 1.0` passes the schema, but the parser requires a real
   integer and returns `invalid_response`.

Case binding: the schema validates structure, vocabulary and the
fixed pins (contract, scenario set version, registry version, plan version).
It cannot know the current case, so the VALUE of `scenario_id` and
`objective_ref` is checked only by `parse_response`. The schema is therefore
equal or more permissive, never stricter. The binding fails closed: a scenario
with no mapped objective, or a non-text `objective_ref`, is `invalid_response`.

## Rule

`check_eligibility(scenario)` is a pure function over the scenario facts. It
calls the existing oracle `derive_expected` (and so `_assess`); no date or
balance logic is duplicated. Output: eligible, or an abstention code.

## Comparison results

| Result | Meaning |
|---|---|
| `agree_proceed` | rule eligible, model proceeds |
| `correct_abstention` | rule abstains, model abstains with the same code |
| `blocked_unsafe_proposal` | model proceeds where the rule abstains |
| `unwarranted_abstention` | model abstains where the rule allows |
| `abstain_code_mismatch` | valid abstention, code differs from the rule; no credit |
| `invalid_response` | malformed or misbound; never read as an abstention |

Only the first two count as a contract hit. The result carries fixed codes and
the scenario id only, `is_grant` is always `False`, there is no scorer label
(and no `admitted`), and nothing from the model answer is copied into it.

## Warning: aggregate hit rate misleads

Do not judge a model by the aggregate `contract_hit` count. A model that always
abstains (with the right code whenever the rule abstains) scores 9/12, and one
that never abstains scores 3/12. Future evaluation must report rates per class
(eligible versus each abstention code, and each of the six results), with
separate thresholds, never one pooled number.

## Flow

`run_proposal_flow` checks the rule first. If the rule says not eligible, the
plan stage is never called, whatever the model answered. Even a comparator that
wrongly reports agreement cannot open it. A model answer never grants
authority and nothing executes.

## Limits

The test that the contract cannot feed the scorer proves only the absence of a
direct import (and of scorer names in the source). It does not prove the absence
of a transitive dependency, since the contract imports the shadow adapter.


Synthetic scenarios only (12). Not model validation. Phase 2 (runtime
integration, real provider channel, scorer credit) needs separate approval.
