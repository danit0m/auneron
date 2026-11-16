"""S3 -- shadow observer of the overdue detection agent (no database).

Covers the isolation exception (exact path, AST proofs), the config
gate, the observer contract and the service hook with stubs only. The
twin-world database tests live in test_overdue_detection_shadow_db.py.
No network, no real model: the gateway is a scripted stub here.
"""

from __future__ import annotations

import ast
import json
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.intelligence.gateway.contracts import DataClassification
from app.intelligence.gateway.contracts import ModelCallResult
from app.services import overdue_detection_service as service_module
from app.services import overdue_detection_shadow as shadow_module
from app.services.overdue_detection_service import OverdueDetectionService
from app.services.overdue_detection_service import OverdueShadowObservation
from app.services.overdue_detection_shadow import OverdueShadowModelObserver

from test_intelligence_boundaries import APP
from test_intelligence_boundaries import APP_FILES_ALLOWED_TO_MENTION_INTELLIGENCE
from test_intelligence_boundaries import BACKEND
from test_intelligence_boundaries import SHADOW_OBSERVER_EXCEPTION
from test_intelligence_boundaries import python_files
from test_intelligence_config_flags import make_settings
from test_intelligence_config_flags import production_settings


pytestmark = pytest.mark.security

SHADOW_FILE = BACKEND / SHADOW_OBSERVER_EXCEPTION
DIGEST = "ab" * 32
SHADOW_ON = dict(
    INTELLIGENCE_MODE="shadow",
    OVERDUE_SHADOW_ENABLED=True,
    OVERDUE_SHADOW_DATA_CLASSIFICATION="synthetic",
    OVERDUE_SHADOW_DEPLOYMENT_BUDGET_ID=1,
    OVERDUE_SHADOW_MODEL_DIGEST=DIGEST,
)


# ----------------------------------------------------------------------
# boundary: the nominal, exact-path exception
# ----------------------------------------------------------------------


def imported_modules(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def intelligence_importers(sources: dict[str, str]) -> list[str]:
    """Files OUTSIDE app/intelligence that import it (AST, not regex)."""
    return sorted(
        path
        for path, text in sources.items()
        if not path.startswith("app/intelligence/")
        and any(
            name == "app.intelligence"
            or name.startswith("app.intelligence.")
            for name in imported_modules(text)
        )
    )


def app_sources() -> dict[str, str]:
    return {
        p.relative_to(BACKEND).as_posix(): p.read_text(encoding="utf-8")
        for p in python_files(APP)
    }


def test_exception_is_one_exact_path() -> None:
    assert SHADOW_OBSERVER_EXCEPTION == (
        "app/services/overdue_detection_shadow.py"
    )
    assert SHADOW_FILE.is_file()
    assert SHADOW_OBSERVER_EXCEPTION in (
        APP_FILES_ALLOWED_TO_MENTION_INTELLIGENCE
    )


def test_only_the_exception_was_added_to_the_intelligence_importers() -> None:
    importers = set(intelligence_importers(app_sources()))

    # Before S3 the allowed set was these five; S3 adds exactly one.
    assert (
        APP_FILES_ALLOWED_TO_MENTION_INTELLIGENCE
        - {SHADOW_OBSERVER_EXCEPTION}
        == {
            "app/core/config.py",
            "app/core/observability.py",
            "app/models/__init__.py",
            "app/models/intelligence_budget.py",
            "app/repositories/intelligence_budget_repository.py",
        }
    )
    assert importers <= APP_FILES_ALLOWED_TO_MENTION_INTELLIGENCE
    assert SHADOW_OBSERVER_EXCEPTION in importers


def test_importer_scanner_flags_any_other_module_and_not_inside() -> None:
    sources = {
        "app/services/overdue_detection_service.py": (
            "from app.intelligence.gateway.gateway import Gateway"
        ),
        "app/core/overdue_detection_maintenance.py": (
            "import app.intelligence.gateway.contracts"
        ),
        "app/api/routes/x.py": "from app.intelligence import gateway",
        "app/services/clean.py": (
            "from app.services.overdue_detection_shadow import X"
        ),
        "app/intelligence/gateway/gateway.py": (
            "from app.intelligence.gateway.contracts import X"
        ),
    }

    assert intelligence_importers(sources) == [
        "app/api/routes/x.py",
        "app/core/overdue_detection_maintenance.py",
        "app/services/overdue_detection_service.py",
    ]


def test_no_other_module_references_the_shadow_module_except_assembly() -> None:
    referrers = sorted(
        path
        for path, text in app_sources().items()
        if "overdue_detection_shadow" in text
        and path != SHADOW_OBSERVER_EXCEPTION
    )

    # The only reference is the lazy import in the maintenance loop.
    assert referrers == ["app/core/overdue_detection_maintenance.py"]


ALLOWED_SHADOW_IMPORT_PREFIXES = (
    "app.intelligence.gateway.",
    "app.database.database",
)


def test_shadow_module_imports_no_execution_repository_or_model() -> None:
    names = imported_modules(SHADOW_FILE.read_text(encoding="utf-8"))
    own = {
        n
        for n in names
        if n == "app" or n.startswith("app.")
    }

    assert own
    for name in own:
        assert name.startswith(ALLOWED_SHADOW_IMPORT_PREFIXES), name

    forbidden = (
        "app.services",
        "app.repositories",
        "app.models",
        "app.agents",
        "app.orchestrator",
        "app.api",
        "app.core",
    )
    assert not [n for n in own if n.startswith(forbidden)]

    text = SHADOW_FILE.read_text(encoding="utf-8")
    for word in (
        "account_mark_overdue_execution_service",
        "human_account_mark_overdue",
        "policy_account_mark_overdue",
        "account_mark_paid_execution_service",
        "approval_service",
        "approval_repository",
    ):
        assert word not in text, word


def test_shadow_module_writes_nothing_of_the_governed_domain() -> None:
    tree = ast.parse(SHADOW_FILE.read_text(encoding="utf-8"))
    identifiers = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }

    for name in (
        "Account",
        "AccountEvent",
        "ApprovalRequest",
        "ApprovalConsumption",
        "ApprovalDecision",
        "Session",
    ):
        assert name not in identifiers, name

    write_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr
        in {"add", "add_all", "commit", "flush", "merge", "delete",
            "execute", "begin", "rollback", "query", "bulk_save_objects"}
    }
    assert write_calls == set()

    # SessionLocal is only ever handed over as a factory, never called.
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "SessionLocal" not in called_names


def test_shadow_module_scanner_catches_a_violating_module() -> None:
    bad = (
        "from app.services.account_mark_overdue_execution_service "
        "import AccountMarkOverdueExecutionService\n"
        "from app.models.account import Account\n"
    )
    own = {n for n in imported_modules(bad) if n.startswith("app.")}

    assert [
        n for n in own if not n.startswith(ALLOWED_SHADOW_IMPORT_PREFIXES)
    ]


def test_execution_and_human_modules_never_see_the_observer() -> None:
    names = [
        "app/services/account_mark_overdue_execution_service.py",
        "app/services/human_account_mark_overdue_execution_service.py",
        "app/services/human_account_mark_overdue_materialization_service.py",
        "app/services/policy_account_mark_overdue_execution_service.py",
        "app/services/account_mark_paid_execution_service.py",
        "app/core/policy_account_mark_overdue_trigger_maintenance.py",
    ]
    for relative in names:
        text = (BACKEND / relative).read_text(encoding="utf-8")
        assert "shadow" not in text.lower(), relative
        assert "intelligence" not in text.lower(), relative


def test_manual_route_builds_the_scan_without_an_observer() -> None:
    tree = ast.parse(
        (APP / "api" / "routes" / "accounts.py").read_text(encoding="utf-8")
    )
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "OverdueDetectionService"
    ]

    assert len(calls) == 1
    assert calls[0].keywords == []
    assert len(calls[0].args) == 1


def test_service_module_does_not_import_the_intelligence_layer() -> None:
    text = Path(service_module.__file__).read_text(encoding="utf-8")

    assert "app.intelligence" not in text
    assert "overdue_detection_shadow" not in text


# ----------------------------------------------------------------------
# config gate
# ----------------------------------------------------------------------


def test_shadow_is_off_by_default() -> None:
    settings = make_settings()

    assert settings.overdue_shadow_enabled is False
    assert settings.overdue_shadow_max_accounts_per_run == 3
    assert shadow_module.build_overdue_shadow_observer(settings) is None


def test_valid_shadow_configuration_is_accepted_outside_production() -> None:
    settings = make_settings(**SHADOW_ON)

    assert settings.overdue_shadow_enabled is True


def test_production_refuses_the_shadow_flag(monkeypatch) -> None:
    with pytest.raises(ValidationError, match="OVERDUE_SHADOW_ENABLED"):
        production_settings(monkeypatch, OVERDUE_SHADOW_ENABLED=True)

    with pytest.raises(ValidationError):
        production_settings(monkeypatch, **SHADOW_ON)


@pytest.mark.parametrize("value", [None, "", "real", "REAL", "anonymized"])
def test_shadow_requires_explicit_synthetic_classification(value) -> None:
    values = dict(SHADOW_ON)

    if value is None:
        del values["OVERDUE_SHADOW_DATA_CLASSIFICATION"]
    else:
        values["OVERDUE_SHADOW_DATA_CLASSIFICATION"] = value

    with pytest.raises(ValidationError, match="synthetic"):
        make_settings(**values)


def test_shadow_requires_intelligence_mode_shadow() -> None:
    values = dict(SHADOW_ON)
    values["INTELLIGENCE_MODE"] = "off"

    with pytest.raises(ValidationError, match="INTELLIGENCE_MODE=shadow"):
        make_settings(**values)


@pytest.mark.parametrize(
    "key, value",
    [
        ("OVERDUE_SHADOW_DEPLOYMENT_BUDGET_ID", None),
        ("OVERDUE_SHADOW_MODEL_DIGEST", ""),
        ("OVERDUE_SHADOW_MODEL_DIGEST", "AB"),
        ("OVERDUE_SHADOW_MODEL_ID", " "),
    ],
)
def test_shadow_requires_budget_digest_and_model(key, value) -> None:
    values = dict(SHADOW_ON)
    values[key] = value

    if value is None:
        del values[key]

    with pytest.raises(ValidationError):
        make_settings(**values)


@pytest.mark.parametrize("value", [0, 21])
def test_account_limit_is_bounded(value) -> None:
    with pytest.raises(ValidationError):
        make_settings(OVERDUE_SHADOW_MAX_ACCOUNTS_PER_RUN=value)


def test_builder_is_fail_closed_when_a_condition_is_missing() -> None:
    settings = make_settings(**SHADOW_ON)

    for patch in (
        {"intelligence_mode": "off"},
        {"overdue_shadow_data_classification": "real"},
        {"overdue_shadow_deployment_budget_id": None},
        {"environment": "production"},
    ):
        view = SimpleNamespace(
            overdue_shadow_enabled=True,
            environment=patch.get("environment", settings.environment),
            intelligence_mode=patch.get(
                "intelligence_mode", settings.intelligence_mode
            ),
            overdue_shadow_data_classification=patch.get(
                "overdue_shadow_data_classification", "synthetic"
            ),
            overdue_shadow_deployment_budget_id=patch.get(
                "overdue_shadow_deployment_budget_id", 1
            ),
        )
        assert shadow_module.build_overdue_shadow_observer(view) is None


def test_builder_prepares_the_local_runtime_without_any_call() -> None:
    observer = shadow_module.build_overdue_shadow_observer(
        make_settings(**SHADOW_ON)
    )

    assert isinstance(observer, OverdueShadowModelObserver)
    assert observer._profile == "local-qwen3-4b"
    assert observer._limit == 3


# ----------------------------------------------------------------------
# observer contract (stub gateway)
# ----------------------------------------------------------------------


def model_result(
    *, ok=True, parsed=None, error_code=None, final_state="settled"
) -> ModelCallResult:
    return ModelCallResult(
        ok=ok,
        parsed=parsed,
        error_code=error_code,
        call_ids=(1,),
        final_state=final_state,
        attempts=1,
        actual_micro=0,
        actual_tokens=0,
    )


class StubGateway:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def call(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)

        if isinstance(outcome, BaseException):
            raise outcome

        return outcome


def observation(account_id=1, approval_id=10, status="aberto"):
    return OverdueShadowObservation(
        account_id=account_id,
        approval_request_id=approval_id,
        due_date="2026-01-10",
        status=status,
        business_date="2026-02-01",
    )


def make_observer(outcomes, limit=3):
    gateway = StubGateway(outcomes)
    return (
        OverdueShadowModelObserver(
            gateway=gateway,
            profile="lab-fast",
            deployment_budget_id=7,
            max_accounts_per_run=limit,
        ),
        gateway,
    )


def shadow_records(caplog):
    return [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "overdue.shadow.observed"
    ]


def test_valid_agreement_is_logged(caplog) -> None:
    observer, gateway = make_observer(
        [model_result(parsed={"decision": "mark_overdue", "reason": "x"})]
    )

    with caplog.at_level(logging.INFO, logger="auneron.overdue_shadow"):
        observer.observe_run([observation()])

    (record,) = shadow_records(caplog)
    assert record.account_id == 1
    assert record.approval_request_id == 10
    assert record.call_key == "overdue-shadow:10"
    assert record.decision_modelo == "mark_overdue"
    assert record.decision_deterministica == "mark_overdue"
    assert record.agreement is True
    assert record.call_ok is True
    assert record.final_state == "settled"
    assert not hasattr(record, "reason")


def test_valid_divergence_is_logged(caplog) -> None:
    observer, _ = make_observer(
        [model_result(parsed={"decision": "no_action", "reason": "x"})]
    )

    with caplog.at_level(logging.INFO, logger="auneron.overdue_shadow"):
        observer.observe_run([observation()])

    (record,) = shadow_records(caplog)
    assert record.agreement is False
    assert record.decision_modelo == "no_action"


@pytest.mark.parametrize(
    "outcome",
    [
        model_result(
            ok=False, parsed=None, error_code="invalid_schema_response"
        ),
        model_result(
            ok=False,
            parsed=None,
            error_code="provider_failure",
            final_state="unknown",
        ),
        # Even a (wrongly) "ok" result with a hostile decision is not a
        # decision: it must be recorded as no output.
        model_result(parsed={"decision": "mark_paid", "reason": "pay it"}),
        model_result(parsed="marcar como pago"),
        model_result(parsed=None),
        RuntimeError("boom"),
    ],
)
def test_hostile_invalid_or_failed_output_is_no_decision(
    outcome, caplog
) -> None:
    observer, _ = make_observer([outcome])

    with caplog.at_level(logging.INFO, logger="auneron.overdue_shadow"):
        observer.observe_run([observation()])

    (record,) = shadow_records(caplog)
    assert record.decision_modelo is None
    assert record.agreement is None
    assert record.call_ok is False


def test_request_is_minimal_synthetic_and_carries_no_personal_data() -> None:
    observer, gateway = make_observer(
        [model_result(parsed={"decision": "mark_overdue", "reason": "x"})]
    )
    observer.observe_run([observation(account_id=987654)])

    (request,) = gateway.requests
    payload = json.loads(request.input_text)

    assert set(payload) == {
        "due_date", "status", "business_date", "days_after_due",
    }
    assert payload["days_after_due"] == 22
    assert "987654" not in request.input_text
    assert request.data_classification is DataClassification.SYNTHETIC
    assert request.deployment_budget_id == 7
    assert request.profile == "lab-fast"
    assert request.purpose == "overdue_shadow_v1"
    assert request.output_schema["properties"]["decision"]["enum"] == [
        "mark_overdue", "no_action",
    ]


def test_observer_refuses_non_synthetic_classification() -> None:
    with pytest.raises(ValueError):
        OverdueShadowModelObserver(
            gateway=StubGateway([]),
            profile="p",
            deployment_budget_id=1,
            data_classification=DataClassification.REAL,
        )


def test_account_limit_per_run_is_respected() -> None:
    ok_result = model_result(
        parsed={"decision": "mark_overdue", "reason": "x"}
    )
    observer, gateway = make_observer([ok_result] * 5, limit=2)

    observer.observe_run(
        [observation(account_id=i, approval_id=100 + i) for i in range(5)]
    )

    assert len(gateway.requests) == 2


def test_duplicates_do_not_consume_the_run_limit() -> None:
    duplicate = model_result(
        ok=False, error_code="duplicate_call_key", final_state=None
    )
    ok_result = model_result(
        parsed={"decision": "mark_overdue", "reason": "x"}
    )
    observer, gateway = make_observer(
        [duplicate, duplicate, ok_result], limit=1
    )

    observer.observe_run(
        [observation(account_id=i, approval_id=200 + i) for i in range(3)]
    )

    assert len(gateway.requests) == 3


def test_budget_exhaustion_stops_the_run() -> None:
    exceeded = model_result(
        ok=False, error_code="budget_exceeded", final_state=None
    )
    observer, gateway = make_observer([exceeded, exceeded], limit=3)

    observer.observe_run(
        [observation(account_id=i, approval_id=300 + i) for i in range(3)]
    )

    assert len(gateway.requests) == 1


def test_one_failure_does_not_stop_the_next_observation(caplog) -> None:
    observer, gateway = make_observer(
        [
            RuntimeError("boom"),
            model_result(
                parsed={"decision": "mark_overdue", "reason": "x"}
            ),
        ]
    )

    with caplog.at_level(logging.INFO, logger="auneron.overdue_shadow"):
        observer.observe_run(
            [observation(1, 1), observation(2, 2)]
        )

    assert len(gateway.requests) == 2
    assert [r.agreement for r in shadow_records(caplog)] == [None, True]


# ----------------------------------------------------------------------
# service hook (stubs, no database)
# ----------------------------------------------------------------------


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self._rows = rows
        self.rollbacks = 0
        self.new = set()
        self.dirty = set()
        self.deleted = set()

    def query(self, *a, **k):
        return _Query(self._rows)

    def rollback(self):
        self.rollbacks += 1


def build_service(monkeypatch, accounts, observer, fail_ids=()):
    db = _Db(accounts)
    service = OverdueDetectionService.__new__(OverdueDetectionService)
    service.db = db
    service.shadow_observer = observer
    monkeypatch.setattr(
        service_module, "resolve_system_principal", lambda _db: object()
    )

    def fake_process(*, account, principal, now, counters):
        if account.id in fail_ids:
            raise ValueError("fails")

        counters.approvals_requested += 1
        counters.proposals_created += 1
        counters.shadow_pending.append(
            OverdueShadowObservation(
                account_id=account.id,
                approval_request_id=account.id * 10,
                due_date=str(account.vencimento),
                status="aberto",
                business_date=counters.business_date,
            )
        )

    monkeypatch.setattr(service, "_process_account", fake_process)
    return service, db


ACCOUNTS = [
    SimpleNamespace(id=1, vencimento=date(2026, 1, 10)),
    SimpleNamespace(id=2, vencimento=date(2026, 1, 11)),
]


class RecordingObserver:
    def __init__(self, raises=None, returns="IGNORED"):
        self.raises = raises
        self.returns = returns
        self.received = []

    def observe_run(self, observations):
        self.received.append(tuple(observations))

        if self.raises is not None:
            raise self.raises

        return self.returns


def test_service_without_observer_is_unchanged(monkeypatch) -> None:
    service, db = build_service(monkeypatch, ACCOUNTS, None)

    result = service.run_scan(today=date(2026, 2, 1))

    assert result.approvals_requested == 2
    assert db.rollbacks == 0


@pytest.mark.parametrize(
    "observer",
    [
        RecordingObserver(),
        RecordingObserver(returns={"decision": "mark_paid"}),
        RecordingObserver(raises=RuntimeError("boom")),
        RecordingObserver(raises=ValueError("timeout")),
    ],
)
def test_counters_are_identical_with_any_observer(monkeypatch, observer):
    base_service, _ = build_service(monkeypatch, ACCOUNTS, None)
    baseline = base_service.run_scan(today=date(2026, 2, 1))

    service, _ = build_service(monkeypatch, ACCOUNTS, observer)
    result = service.run_scan(today=date(2026, 2, 1))

    assert result == baseline
    assert result.as_dict() == baseline.as_dict()


def test_observer_gets_plain_values_after_the_decision(monkeypatch) -> None:
    observer = RecordingObserver()
    service, db = build_service(monkeypatch, ACCOUNTS, observer)

    service.run_scan(today=date(2026, 2, 1))

    (received,) = observer.received
    assert [o.account_id for o in received] == [1, 2]
    assert all(isinstance(o, OverdueShadowObservation) for o in received)
    assert received[0].business_date == "2026-02-01"
    assert received[0].deterministic_decision == "mark_overdue"
    # The detection transaction was released before the observer ran.
    assert db.rollbacks == 1


def test_failed_account_is_not_observed(monkeypatch) -> None:
    observer = RecordingObserver()
    service, _ = build_service(
        monkeypatch, ACCOUNTS, observer, fail_ids={1}
    )

    result = service.run_scan(today=date(2026, 2, 1))

    assert result.failures == 1
    (received,) = observer.received
    assert [o.account_id for o in received] == [2]


def test_observer_is_not_called_when_nothing_was_decided(monkeypatch) -> None:
    observer = RecordingObserver()
    service, _ = build_service(monkeypatch, [], observer)

    service.run_scan(today=date(2026, 2, 1))

    assert observer.received == []


def test_pending_writes_are_never_rolled_back_by_the_hook(monkeypatch) -> None:
    observer = RecordingObserver()
    service, db = build_service(monkeypatch, ACCOUNTS, observer)
    db.new = {object()}

    service.run_scan(today=date(2026, 2, 1))

    # Failures after the account loop use the same rule: with pending
    # writes the hook does not touch the session, it only observes.
    assert db.rollbacks == 0
    assert len(observer.received) == 1


def test_hook_failure_is_logged_not_raised(monkeypatch, caplog) -> None:
    service, _ = build_service(
        monkeypatch, ACCOUNTS, RecordingObserver(raises=RuntimeError("x"))
    )

    with caplog.at_level(
        logging.WARNING, logger="auneron.overdue_detection"
    ):
        service.run_scan(today=date(2026, 2, 1))

    assert any(
        getattr(r, "event", None) == "overdue.shadow.observer_failed"
        for r in caplog.records
    )
