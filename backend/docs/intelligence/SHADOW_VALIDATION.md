# Area 3 isolated validation — 2026-11-03

## Observed candidate evidence

- Planning, projection, bridge and simulation: 90 tests passed in 67.94s.
  Test-side assembly of the real application policy was explicitly replaced by
  a synthetic PolicyReference. This is not real-policy integration evidence.
- Historical record of the previous freeze (scorer v3, adapter v2, freeze 1;
  superseded by scorer v4, adapter v3, freeze 2, whose test count is not
  recorded here). Scenarios v2, scorer v3, adapter v2 and freeze manifest: 264
  tests passed in 3.74s, with the actual planning registry/schema and synthetic outputs.
- Seven earlier binding checks passed (valid read-only control; foreign
  objective/version/bool version/account; stale/tampered projection).
- Isolated runners disabled pytest plugin autoload and repository conftest,
  blocked application DB/network imports and socket connections, and reported
  forbidden_loaded=[] and blocked_attempts=[].
- git diff --check passed before publication.

The runners are local review tools, not an alternate CI configuration.
Normal CI must still exercise the real policy and the required backend,
frontend, PostgreSQL recovery, E2E and cleanup gates.

## Corrections found during review

The closed projection rejects invalid shape/fabricated authority before
planning admission. Related conformance and proposal-simulation tests now
require that earlier rejection; independent validator tests still prove
no grant and typed refusal. The first PR #17 CI stopped after 2,566 passes
at a stale simulation assertion (run 41740179417); no merge followed it.

Scenario s06 formerly expected an unavailable escalation capability.
The reviewed v2 explicitly abstains; scorer step limit now matches the
eight-step plan limit. Conservative text refusals and unevaluable evidence
are distinct from observed inappropriate mutation; all block the gate.
The matrix gate is reachable through scripted permitted plans and an
explicit synthetic abstention envelope. Read-only plans are not abstention.

The PO made narrow test-only corrections for duplicate helper arguments,
schema-earlier refusal expectations and concrete coverage for new rejection
codes, and added the frozen-manifest regression. This records the reviewer
test-authorship exception; it does not represent independent runtime authorship.

## Freeze

SHADOW_EVALUATION_FREEZE.json pins the scenario-set digest and normalized
UTF-8/LF hashes of the scenarios, scorer and adapter. The dedicated test checks
the manifest so future fixture/scorer changes require explicit re-review.

## Boundary and remaining work

These are reviewed synthetic test tools. They do not execute plans, create
authority, call a model, connect to a provider or prove model quality.
The adapter normalizes parsed plan.v1 but does not supply admission from the
real bridge. The abstention envelope exists only for synthetic evaluation.
A real run still needs a defined model-facing output channel, provider/adapter,
price and cost authority, credentials held outside the inert packages, and
bounded disposable-environment execution. No paid call or live grant is
authorized by this record. Area 3 remains open until those gates are met.
