"""
Despacho concorrente do mesmo pedido aprovado (defeito 3 da homologacao de
19/11): o despacho consultivo e a recuperacao do piloto varrem o mesmo
pedido ao mesmo tempo. A segunda tentativa deve sair limpa (sem WARNING
de falha), mantendo o efeito unico garantido pelo ApprovalConsumption.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest import mock

import pytest

from app.core import approval_dispatch_exclusion as exclusion
from app.core import authenticated_advisory_dispatch_maintenance as maintenance
from app.core.config import settings


def test_advisory_key_is_stable_and_validates_input() -> None:
    assert exclusion.advisory_key(9) == exclusion.advisory_key(9)
    assert exclusion.advisory_key(9) != exclusion.advisory_key(10)
    for invalid in (0, -1, True, "9", None):
        with pytest.raises(ValueError):
            exclusion.advisory_key(invalid)  # type: ignore[arg-type]


def _run_with_exclusion(sole: bool, caplog):
    request = SimpleNamespace(id=9, idempotency_key="advisory:1:2")
    repository = mock.MagicMock()
    repository.list_approved_agent_requests_without_consumption.side_effect = [
        [request],
        [],
    ]

    class _Exclusion:
        def __enter__(self):
            return sole

        def __exit__(self, *exc):
            return False

    with (
        mock.patch.object(maintenance, "SessionLocal"),
        mock.patch.object(
            maintenance, "ApprovalRepository", return_value=repository
        ),
        mock.patch.object(
            maintenance, "AuthenticatedAdvisoryProposalApprovalBridgeService"
        ),
        mock.patch.object(
            maintenance,
            "exclusive_approval_dispatch",
            return_value=_Exclusion(),
        ),
        mock.patch.object(
            maintenance, "_dispatch_candidate", return_value=True
        ) as dispatch,
        caplog.at_level(logging.WARNING, logger="auneron.advisory_dispatch"),
    ):
        dispatched = maintenance.run_advisory_dispatch_recovery(limit=5)
    return dispatched, dispatch, caplog


def test_second_attempt_skips_cleanly_without_failure_warning(caplog) -> None:
    dispatched, dispatch, caplog = _run_with_exclusion(False, caplog)

    assert dispatched == 0
    dispatch.assert_not_called()
    assert not [
        record
        for record in caplog.records
        if "advisory_dispatch_recovery_failed" in record.getMessage()
    ]


def test_sole_attempt_dispatches_once(caplog) -> None:
    dispatched, dispatch, _ = _run_with_exclusion(True, caplog)

    assert dispatched == 1
    dispatch.assert_called_once()


@pytest.mark.skipif(
    not settings.is_postgresql, reason="advisory lock exige PostgreSQL"
)
def test_postgres_lock_is_exclusive_per_request_and_released() -> None:
    with exclusion.exclusive_approval_dispatch(987654) as first:
        assert first is True
        with exclusion.exclusive_approval_dispatch(987654) as second:
            assert second is False
        # Outro pedido nao e afetado.
        with exclusion.exclusive_approval_dispatch(987655) as other:
            assert other is True

    # Liberado ao sair: nova tentativa obtem o lock.
    with exclusion.exclusive_approval_dispatch(987654) as again:
        assert again is True
