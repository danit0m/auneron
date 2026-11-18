"""S3 -- twin-world database tests for the overdue shadow observer.

PostgreSQL required (runs in CI). No network and no real model: the
provider is the in-process FakeProvider behind the real gateway and the
real budget ledger. Each hostile scenario is run twice from an empty
database -- once without shadow, once with -- and the governed state is
compared field by field.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from sqlalchemy import func
from sqlalchemy import select

from conftest import _clean_database

from app.core import overdue_detection_maintenance as maintenance
from app.database.database import SessionLocal
from app.intelligence.gateway.adapters.fake import FakeProvider
from app.intelligence.gateway.adapters.fake import ok
from app.intelligence.gateway.adapters.fake import raw
from app.intelligence.gateway.adapters.fake import timeout
from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.budget import utcnow
from app.intelligence.gateway.gateway import Gateway
from app.intelligence.gateway.gateway import GatewayLimits
from app.intelligence.gateway.profiles import ModelProfile
from app.intelligence.gateway.profiles import ProfileRegistry
from app.intelligence.gateway.profiles import (
    build_local_ollama_profile,
)
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.models.intelligence_budget import IntelligenceModelCall
from app.services.overdue_detection_service import OverdueDetectionService
from app.services.overdue_detection_shadow import OverdueShadowModelObserver

from test_f1_overdue_system_principal import _make_account
from test_f1_overdue_system_principal import _make_admin
from test_f1_overdue_system_principal import _make_system_principal
from test_f1_overdue_system_principal import _seed_overdue_skill


pytestmark = pytest.mark.contract

DAYS = 41


def shadow_test_profile():
    """
    Fake-provider profile that mirrors the REAL shadow profile's output
    cap (reasoning + JSON = 4096). The lab profiles (cap 512/1024) would
    be refused in the gateway preflight, so the shadow would never call
    the provider. Name/price/model are the lab-fast ones.
    """
    real = build_local_ollama_profile()
    return ModelProfile(
        name="lab-fast",
        provider="fake",
        model_id="fake-small-v0",
        price_key="fake-small",
        max_output_tokens_cap=real.max_output_tokens_cap,
        think=real.think,
        sampling=real.sampling,
    )


def make_observer(script, *, limit=3):
    ledger = BudgetLedger(
        SessionLocal, unknown_fraction_bps=2500, recovery_grace_seconds=30
    )
    now = utcnow()
    budget_id = ledger.create_budget(
        scope_kind="deployment_period",
        scope_ref="overdue-shadow-test",
        limit_micro=10_000_000,
        limit_tokens=10_000_000,
        period_start=now - timedelta(hours=1),
        period_end=now + timedelta(hours=1),
    )
    provider = FakeProvider(script)
    gateway = Gateway(
        registry=ProfileRegistry([shadow_test_profile()]),
        adapters={"fake": provider},
        ledger=ledger,
        limits=GatewayLimits(1, 5, 2000),
        mode_provider=lambda: "shadow",
    )
    observer = OverdueShadowModelObserver(
        gateway=gateway,
        profile="lab-fast",
        deployment_budget_id=budget_id,
        max_accounts_per_run=limit,
    )
    return observer, provider


class RaisingObserver:
    def observe_run(self, observations):
        raise RuntimeError("observer exploded")


def seed(db_session, *, accounts=1):
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    made = [_make_account(db_session, days_overdue=DAYS) for _ in range(accounts)]
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)
    return made


def release(db_session):
    """
    End the session's transaction and drop its identity map. Reads keep
    an open transaction (and its locks) until this runs; TRUNCATE in
    _clean_database() uses another connection and would wait forever on
    them (LockNotAvailable in CI).
    """
    db_session.rollback()
    db_session.expire_all()
    db_session.close()


def governed_state(db_session):
    release(db_session)
    state = _collect_state(db_session)
    release(db_session)
    return state


def _collect_state(db_session):
    # Ids are comparable across worlds: _clean_database() truncates with
    # RESTART IDENTITY (accounts, users, skills, approvals, proposals),
    # and fingerprint/digests derive only from those ids, the manifest
    # digest and the account fields (no clock/uuid in the proposal
    # snapshot, approval fingerprint or input digest). expires_at /
    # created_at are the only time-based columns and are NOT compared.
    return {
        "accounts": [
            (a.id, a.status, str(a.vencimento))
            for a in db_session.query(Account).order_by(Account.id)
        ],
        "events": db_session.scalar(
            select(func.count()).select_from(AccountEvent)
        ),
        "requests": [
            (
                r.id,
                r.status,
                r.requester_actor_type,
                r.requester_reference,
                r.idempotency_key,
                r.request_fingerprint,
                r.input_digest,
                r.target_account_id,
            )
            for r in db_session.query(ApprovalRequest).order_by(
                ApprovalRequest.id
            )
        ],
        "decisions": db_session.scalar(
            select(func.count()).select_from(ApprovalDecision)
        ),
        "consumptions": db_session.scalar(
            select(func.count()).select_from(ApprovalConsumption)
        ),
        "proposals": [
            (
                p.id,
                p.idempotency_key,
                p.authority_source,
                p.snapshot_digest,
                p.snapshot_payload,
            )
            for p in db_session.query(
                AuthenticatedAdvisoryProposal
            ).order_by(AuthenticatedAdvisoryProposal.id)
        ],
    }


def run_world(db_session, make_observer_fn=None):
    """
    The observer is built AFTER the truncate: its budget row lives in
    intelligence_budgets, which _clean_database() wipes.
    """
    release(db_session)
    _clean_database()
    observer = make_observer_fn() if make_observer_fn else None
    seed(db_session)
    result = OverdueDetectionService(
        db_session, shadow_observer=observer
    ).run_scan()
    counts = result.as_dict()
    return counts, governed_state(db_session)


SCENARIOS = {
    "hostile_mark_paid": [ok({"decision": "mark_paid", "reason": "pay"})],
    "hostile_text": [raw("marcar como pago agora")],
    "timeout": [timeout()],
    "invalid_schema": [ok({"unexpected": True})],
    "valid_agree": [ok({"decision": "mark_overdue", "reason": "past due"})],
    "valid_diverge": [ok({"decision": "no_action", "reason": "x"})],
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_governed_state_is_identical_with_and_without_shadow(
    db_session, name
) -> None:
    baseline_counts, baseline_state = run_world(db_session, None)
    made = {}

    def factory():
        made["observer"], made["provider"] = make_observer(SCENARIOS[name])
        return made["observer"]

    shadow_counts, shadow_state = run_world(db_session, factory)
    provider = made["provider"]

    assert len(provider.calls) == 1  # the model really was consulted
    assert shadow_counts == baseline_counts
    assert shadow_state == baseline_state
    assert baseline_state["accounts"][0][1] == "aberto"
    assert baseline_state["events"] == 0
    assert baseline_state["consumptions"] == 0
    assert baseline_state["decisions"] == 0


def test_observer_exception_changes_nothing(db_session) -> None:
    baseline_counts, baseline_state = run_world(db_session, None)
    shadow_counts, shadow_state = run_world(db_session, RaisingObserver)

    assert shadow_counts == baseline_counts
    assert shadow_state == baseline_state


def test_agent_origin_keeps_requester_and_idempotency(db_session) -> None:
    counts, state = run_world(
        db_session,
        lambda: make_observer(SCENARIOS["valid_agree"])[0],
    )

    (request,) = state["requests"]
    (proposal,) = state["proposals"]

    assert counts["aprovacoes_solicitadas"] == 1
    assert request[1] == "pending"  # still waiting for a human
    assert request[2] == "agent"
    assert request[3] == "agent:OverdueDetectionAgent"
    assert request[4] == f"advisory:{proposal[0]}:1"
    assert proposal[2] == "system_principal"
    assert proposal[1].startswith("conta_vencida:1:")
    assert proposal[1].endswith(":attempt:1")

    # A later scan without any observer reuses exactly these rows.
    again = OverdueDetectionService(db_session).run_scan()
    assert again.proposals_created == 0
    assert again.approvals_requested == 0
    assert again.approvals_reused == 1
    assert governed_state(db_session) == state


def test_valid_answer_is_recorded_in_ledger_and_log(
    db_session, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="auneron.overdue_shadow"):
        run_world(
            db_session,
            lambda: make_observer(SCENARIOS["valid_diverge"])[0],
        )

    (record,) = [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "overdue.shadow.observed"
    ]
    assert record.decision_modelo == "no_action"
    assert record.decision_deterministica == "mark_overdue"
    assert record.agreement is False
    assert record.account_id == 1
    assert record.call_key == f"overdue-shadow:{record.approval_request_id}"

    with SessionLocal() as db:
        (call,) = db.query(IntelligenceModelCall).all()
        assert call.state == "settled"
        assert call.provider == "fake"
        assert call.purpose == "overdue_shadow_v2"
        assert call.call_key.startswith("overdue-shadow:")
        assert call.data_classification == "synthetic"


def test_account_limit_per_run_with_real_ledger(db_session) -> None:
    release(db_session)
    _clean_database()
    seed(db_session, accounts=5)
    answer = ok({"decision": "mark_overdue", "reason": "x"})
    observer, provider = make_observer([answer] * 5, limit=2)

    result = OverdueDetectionService(
        db_session, shadow_observer=observer
    ).run_scan()

    assert result.accounts_checked == 5
    assert result.approvals_requested == 5
    assert len(provider.calls) == 2

    with SessionLocal() as db:
        assert db.query(IntelligenceModelCall).count() == 2


def test_rescan_does_not_consult_the_model_again(db_session) -> None:
    release(db_session)
    _clean_database()
    seed(db_session)
    observer, provider = make_observer(
        [ok({"decision": "mark_overdue", "reason": "x"})] * 3
    )

    for _ in range(2):
        OverdueDetectionService(
            db_session, shadow_observer=observer
        ).run_scan()

    # Second scan only reuses the pending approval: nothing to observe.
    assert len(provider.calls) == 1


def test_detection_session_is_released_before_the_model_call(
    db_session,
) -> None:
    release(db_session)
    _clean_database()
    seen = {}

    class Spy:
        def observe_run(self, observations):
            seen["in_transaction"] = db_session.in_transaction()
            seen["count"] = len(observations)
            with SessionLocal() as other:
                seen["approval_exists"] = (
                    other.query(ApprovalRequest)
                    .filter(
                        ApprovalRequest.id
                        == observations[0].approval_request_id
                    )
                    .count()
                    == 1
                )

    seed(db_session)
    OverdueDetectionService(db_session, shadow_observer=Spy()).run_scan()

    assert seen == {
        "in_transaction": False,
        "count": 1,
        "approval_exists": True,  # observed only AFTER the decision
    }


# ----------------------------------------------------------------------
# origins that must never reach the model
# ----------------------------------------------------------------------


@pytest.fixture
def model_is_forbidden(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the model must not be consulted here")

    monkeypatch.setattr(Gateway, "call", boom)
    monkeypatch.setattr(OverdueShadowModelObserver, "observe_run", boom)


def test_manual_origin_does_not_consult_the_model(
    db_session, model_is_forbidden
) -> None:
    # What POST /accounts/detect-overdue builds: no observer.
    release(db_session)
    _clean_database()
    seed(db_session)

    result = OverdueDetectionService(db_session).run_scan()

    assert result.approvals_requested == 1


def test_maintenance_without_flag_builds_no_observer(monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_shadow_observer", None)
    monkeypatch.setattr(maintenance, "_shadow_observer_built", False)

    assert maintenance.settings.overdue_shadow_enabled is False
    assert maintenance._resolve_shadow_observer() is None


def test_l3_origin_does_not_consult_the_model(
    db_session, model_is_forbidden
) -> None:
    from datetime import date
    from datetime import datetime
    from datetime import timezone

    from app.core.policy_account_mark_overdue_trigger_maintenance import (
        run_policy_account_mark_overdue_trigger,
    )
    from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
    from app.models.user import User
    from app.services.policy_authority_grant_service import (
        PolicyAuthorityGrantService,
    )
    from scripts.register_account_mark_overdue_skill import (
        main as register_account_mark_overdue_skill,
    )

    release(db_session)
    _clean_database()
    register_account_mark_overdue_skill()
    granter = User(
        name="Shadow L3 Granter",
        email="shadow-l3-granter@example.com",
        password_hash="not-used",
        role="administrator",
        active=True,
    )
    db_session.add(granter)
    db_session.commit()
    PolicyAuthorityGrantService(db_session).create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    account = Account(
        cliente="Cliente L3 Shadow",
        email="l3-shadow@example.com",
        whatsapp=None,
        valor=10,
        vencimento=date.today() - timedelta(days=5),
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    account_id = account.id  # read before release() detaches it

    release(db_session)  # the trigger runs on its own connections

    result = run_policy_account_mark_overdue_trigger()

    assert result.executions_succeeded == 1
    db_session.expire_all()
    assert db_session.get(Account, account_id).status == "atrasado"
