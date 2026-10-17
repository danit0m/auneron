# Gateway contract (INTELLIGENCE-IMPL-0)

## Rules

1. **Structured output only.** The response must be a JSON document valid
   against the request's JSON Schema (Draft 2020-12). Duplicate keys, `NaN`
   and `Infinity` are rejected by a strict loader. The gateway never executes
   provider tools.
2. **Logical profiles.** Callers name a profile (`lab-fast`, `lab-strong`),
   never a provider model. Every profile needs a pinned price entry.
3. **Fail-closed modes.** `off` (default) refuses every call; `shadow` is
   non-production only; anything else is refused.
4. **Synthetic data only.** `REAL_DATA_ALLOWED` is a code constant (`False`).
   Only the `DataClassification.SYNTHETIC` enum member is accepted; the
   database also restricts `data_classification` to `synthetic`.
5. **No transaction during the provider call.** Reserve (T1) and the
   write-ahead mark (T2) are committed before the adapter is invoked;
   settlement (T3/T4/T5) happens in new transactions afterwards.
6. **Retries and fallbacks** take a new reservation per attempt, only after
   availability failures (timeout, transport error, 429, 5xx, proven not
   sent). Never after refusal, truncation, invalid schema or a budget
   rejection. Each retry moves to the next fallback profile when one remains.
   An open circuit skips a profile without consuming an attempt.
7. **Charged outcomes.** Refusal, truncation and schema-invalid answers are
   settled (charged) because the provider billed them.
8. **No content in logs, errors or the database.** Only an allowlist of
   scalar fields is logged; errors carry stable codes; only SHA-256 digests
   are stored.

## FakeProvider is impossible to select in production

`assert_fake_provider_allowed()` (in `profiles.py`) is the only decision
point. `is_production_environment()` is true when **either** the parsed
`Settings.environment` or the raw `APP_ENV` variable says production.
It is called by four independent layers:

1. `ProfileRegistry` validation of every profile with provider `fake`.
2. The adapter factory `create_adapter("fake")`.
3. `FakeProvider.__init__` and `FakeProvider.invoke`.
4. `Gateway.__init__` and every `Gateway._adapter_for` (any adapter that is a
   `FakeProvider`, declares `provider_kind == "fake"` or reports
   `is_fake`), so subclasses that skip `__init__` or spoof flags are caught.

Additionally the database restricts `intelligence_model_calls.provider` to
`fake`, and `INTELLIGENCE_MODE` must be `off` in production.

## Settings and lab values

All values below are **lab values for tests only**: they are conservative,
carry no budget authority, and every one needs explicit PO approval before it
is used with a real provider.

| Setting | Value | Justification | Application limit | Test | Future approval |
|---|---|---|---|---|---|
| `INTELLIGENCE_MODE` | `off` | Fail-closed default | `off`/`shadow` only; production `off` | `test_intelligence_config_flags.py` | Needed for `governed`/`bounded` |
| `INTELLIGENCE_MAX_ATTEMPTS` | 2 | One retry/fallback bounds spend amplification to 2x | 1-3 | `test_intelligence_gateway_contract.py` | Yes |
| `INTELLIGENCE_CALL_TIMEOUT_SECONDS` | 30 | Bounds how long a reservation stays in flight; also the recovery deadline | 1-120 | `test_intelligence_budget_recovery.py` | Yes |
| `INTELLIGENCE_MAX_CONCURRENT_CALLS` | 2 | Limits parallel exposure | 1-8 | `test_intelligence_gateway_contract.py` | Yes |
| `INTELLIGENCE_RESERVATION_MARGIN_BPS` | 2000 (20%) | Headroom over the priced worst case | 0-10000 | `test_intelligence_gateway_contract.py` | Yes |
| `INTELLIGENCE_UNKNOWN_FRACTION_BPS` | 2500 (25%) | Stops admission when unknown consumption reaches a quarter of a limit | 1-10000 | `test_intelligence_budget_ledger.py` | Yes |
| `INTELLIGENCE_RECOVERY_GRACE_SECONDS` | 30 | Avoids recovering a slow call that is still running | 0-3600 | `test_intelligence_budget_recovery.py` | Yes |
| `INTELLIGENCE_BREAKER_FAILURE_THRESHOLD` | 3 | Consecutive availability failures before opening | 1-20 | `test_intelligence_gateway_contract.py` | Yes |
| `INTELLIGENCE_BREAKER_COOLDOWN_SECONDS` | 30 | Half-open probe interval | 1-3600 | `test_intelligence_gateway_contract.py` | Yes |

Code-level lab limits (not settings): request limit 32,768 bytes, response
limit 65,536 bytes, `call_key` limit 120 characters, price table entries
`fake-small` (1 / 4 USD per Mtok) and `fake-large` (5 / 20 USD per Mtok) which
are invented numbers for the FakeProvider and must never be used as prices.

## Not in this phase

Real adapters, credentials, external hard cap and invoice reconciliation,
`intelligence_budget_entries` (immutable history), `plan.v1`, the objective
manager, approval binding, executor designation and any route or permission.
