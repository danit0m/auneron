# Intelligence gateway foundation (INTELLIGENCE-IMPL-0)

Local, fail-closed foundation of the multi-model gateway. It contains **no**
real provider adapter, **no** external call, **no** LLM credential, **no** new
route or permission, and it is not imported by `app/main.py`.

## Layout

| Path | Role |
|---|---|
| `app/intelligence/gateway/contracts.py` | Request/response contracts, `REAL_DATA_ALLOWED` (code constant, `False`). |
| `app/intelligence/gateway/errors.py` | `GatewayError` codes and provider failure classes. |
| `app/intelligence/gateway/profiles.py` | Logical model profiles and the central FakeProvider production guard. |
| `app/intelligence/gateway/pricing.py` | Integer micro-USD pricing and worst-case reservation. |
| `app/intelligence/gateway/budget.py` | Transactional budget ledger (T1-T8, recovery). |
| `app/intelligence/gateway/circuit_breaker.py`, `concurrency.py` | Availability breaker and bounded concurrency. |
| `app/intelligence/gateway/redaction.py` | Log allowlist and digests. |
| `app/intelligence/gateway/gateway.py` | Orchestration: reserve, write-ahead mark, call, settle/unknown/release. |
| `app/intelligence/gateway/adapters/` | `ProviderAdapter` base and the only adapter, `FakeProvider`. |
| `app/models/intelligence_budget.py`, `app/repositories/intelligence_budget_repository.py` | Persistence (flush only; the ledger owns commit/rollback). |

## Modes

`INTELLIGENCE_MODE`: `off` (default) | `shadow`. `shadow` is refused in
production; `governed` and `bounded` are refused at startup and by the
gateway. `REAL_DATA_ALLOWED` is a constant in code, not configuration.

## Running the tests

Use the standard backend procedure (`scripts/test.ps1`: `APP_ENV=test`,
database `auneron_test`). Markers: `contract`, `security`, `concurrency`.

See `GATEWAY_CONTRACT.md` and `BUDGET_LEDGER.md`.
