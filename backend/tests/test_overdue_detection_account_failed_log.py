"""run_scan must log a structured WARNING when an account fails.

Uses stubs only (no database required).
"""

from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace

from app.services import overdue_detection_service as module
from app.services.overdue_detection_service import OverdueDetectionService


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.rollbacks = 0

    def query(self, *args, **kwargs):
        return _FakeQuery(self._rows)

    def rollback(self):
        self.rollbacks += 1


def test_run_scan_logs_account_failure(monkeypatch, caplog):
    account = SimpleNamespace(id=42, vencimento=date(2026, 1, 10))
    db = _FakeDb([account])
    service = OverdueDetectionService.__new__(OverdueDetectionService)
    service.db = db

    monkeypatch.setattr(
        module, "resolve_system_principal", lambda _db: object()
    )

    def _boom(**kwargs):
        raise ValueError("x" * 500)

    monkeypatch.setattr(service, "_process_account", _boom)

    with caplog.at_level(logging.WARNING, logger="auneron.overdue_detection"):
        result = service.run_scan(today=date(2026, 2, 1))

    assert result.failures == 1
    assert result.accounts_checked == 1
    assert db.rollbacks == 1

    records = [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "overdue.detection.account_failed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    assert record.account_id == 42
    assert record.due_date == "2026-01-10"
    assert record.error_type == "ValueError"
    assert len(record.error_message) == 300
