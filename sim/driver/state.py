"""
StateStore do Driver (HG-1): SQLite em modo WAL, em volume Docker NATIVO
(SIM-CLOCK-1 vale tambem para o estado: nunca bind mount no Windows).

Escreve-antes-de-enviar: cada op passa por pending -> sent -> done e uma op em
`sent` sem `done` e SEMPRE reconciliada antes de qualquer reenvio.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

PENDING, SENT, DONE, RECONCILED, FAILED = "pending", "sent", "done", "reconciled", "failed"
TERMINAL = (DONE, RECONCILED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refs (ref TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS due (rec_ref TEXT PRIMARY KEY, due_day INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ops (
    seq INTEGER PRIMARY KEY, day INTEGER NOT NULL, at TEXT NOT NULL, op TEXT NOT NULL,
    actor TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER, note TEXT, updated_real REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (persona TEXT PRIMARY KEY, cookie TEXT NOT NULL, expires_at TEXT NOT NULL);
"""


class StateStore:
    def __init__(self, path, durable: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL" if durable else "PRAGMA synchronous=OFF")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- meta ---------------------------------------------------------------
    def set_meta(self, key: str, value) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # -- refs / vencimentos -------------------------------------------------
    def set_ref(self, ref: str, value: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO refs VALUES (?, ?)", (ref, json.dumps(value, sort_keys=True)))

    def get_ref(self, ref: str):
        with self._lock:
            row = self._db.execute("SELECT value FROM refs WHERE ref = ?", (ref,)).fetchone()
        return json.loads(row[0]) if row else None

    def all_refs(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT ref, value FROM refs ORDER BY ref").fetchall()
        return {ref: json.loads(value) for ref, value in rows}

    def mapped_account_ids(self) -> set:
        return {value["account_id"] for value in self.all_refs().values() if "account_id" in value}

    def set_due(self, rec_ref: str, due_day: int) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO due VALUES (?, ?)", (rec_ref, due_day))

    def all_due(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT rec_ref, due_day FROM due ORDER BY rec_ref").fetchall()
        return {rec_ref: due_day for rec_ref, due_day in rows}

    def get_due(self, rec_ref: str):
        with self._lock:
            row = self._db.execute("SELECT due_day FROM due WHERE rec_ref = ?", (rec_ref,)).fetchone()
        return row[0] if row else None

    # -- ops ----------------------------------------------------------------
    def register_ops(self, ops: list) -> None:
        with self._lock:
            self._db.execute("BEGIN")
            for op in ops:
                self._db.execute(
                    "INSERT OR IGNORE INTO ops (seq, day, at, op, actor, status, updated_real) VALUES (?,?,?,?,?,?,?)",
                    (op["seq"], op["day"], op["at"], op["op"], op["actor"], PENDING, time.time()))
            self._db.execute("COMMIT")

    def op_status(self, seq: int):
        with self._lock:
            row = self._db.execute("SELECT status FROM ops WHERE seq = ?", (seq,)).fetchone()
        return row[0] if row else None

    def mark(self, seq: int, status: str, *, http_status=None, note=None, attempts=None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE ops SET status = ?, http_status = COALESCE(?, http_status), note = COALESCE(?, note), "
                "attempts = COALESCE(?, attempts), updated_real = ? WHERE seq = ?",
                (status, http_status, note, attempts, time.time(), seq))

    def pending_before(self, day: int, at: str) -> list:
        """Ops NAO terminais estritamente antes do slot (day, at)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT seq FROM ops WHERE status NOT IN (?, ?) AND (day < ? OR (day = ? AND at < ?)) ORDER BY seq",
                (DONE, RECONCILED, day, day, at)).fetchall()
        return [row[0] for row in rows]

    def counts(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) FROM ops GROUP BY status").fetchall()
        return {status: count for status, count in rows}

    def in_flight(self) -> list:
        with self._lock:
            rows = self._db.execute("SELECT seq FROM ops WHERE status = ? ORDER BY seq", (SENT,)).fetchall()
        return [row[0] for row in rows]

    # -- sessoes ------------------------------------------------------------
    def save_session(self, persona: str, cookie: str, expires_at: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?)", (persona, cookie, expires_at))

    def load_session(self, persona: str):
        with self._lock:
            row = self._db.execute("SELECT cookie, expires_at FROM sessions WHERE persona = ?", (persona,)).fetchone()
        return (row[0], row[1]) if row else None

    def drop_session(self, persona: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE persona = ?", (persona,))
