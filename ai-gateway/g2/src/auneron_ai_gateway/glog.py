"""Log JSON minimo. Lista fechada de campos; nada de entrada, saida, cabecalhos ou certificados."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import time
from pathlib import Path
from typing import Any

from .errors import ERROR_STATUS
from .profiles import PROFILES

EVENTS = frozenset({"startup", "shutdown", "request", "conn_refused", "conn_limit", "internal"})
_EXTRA_CODES = frozenset(
    {
        "ok",
        "client_gone",
        "client_cert_missing",
        "client_cert_not_allowed",
        "handshake_failed",
        "ok_table",
        "ok_not_listening",
        "ok_probe",
        "unverifiable_allowed",
        "connection_limit",
        "unexpected_exception",
        "loop_exception",
        "ollama_exposed",
        "exposure_unverifiable",
        "exposure_recovered",
    }
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{7,63}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_INT_FIELDS = ("status", "in_bytes", "out_bytes", "ms_total", "ms_queue", "ms_model")


class AuditLog:
    def __init__(self, directory: Path, retention_days: int = 30) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger(f"auneron_ai_gateway.audit.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = logging.handlers.TimedRotatingFileHandler(
            directory / "gateway.log",
            when="midnight",
            backupCount=retention_days,
            encoding="utf-8",
            utc=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._handler = handler
        self.retention_days = retention_days

    def event(self, event: str, **fields: Any) -> None:
        if event not in EVENTS:
            event = "internal"
        rec: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
        }
        rid = fields.get("request_id")
        if isinstance(rid, str) and _ID.match(rid):
            rec["request_id"] = rid
        prof = fields.get("profile")
        if isinstance(prof, str) and prof in PROFILES:
            rec["profile"] = prof
        dig = fields.get("digest")
        if isinstance(dig, str) and _HEX.match(dig):
            rec["digest"] = dig
        for name in _INT_FIELDS:
            v = fields.get(name)
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10**9:
                rec[name] = v
        code = fields.get("code")
        if isinstance(code, str) and (code in ERROR_STATUS or code in _EXTRA_CODES):
            rec["code"] = code
        try:
            self._logger.info(json.dumps(rec, separators=(",", ":"), sort_keys=True))
        except Exception:  # log nunca derruba o servico
            pass

    def close(self) -> None:
        self._handler.close()
        self._logger.removeHandler(self._handler)
