"""Log de evidencia coletada: JSONL append-only com hash encadeado (autocontido)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

GENESIS = "0" * 64


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def line_hash(record: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in record.items() if k != "hash"})).hexdigest()


class ChainLog:
    def __init__(self, path, fsync: bool = True) -> None:
        self.path = Path(path)
        self._fsync = fsync
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._n, self._tail = 0, GENESIS
        if self.path.exists():
            ok, count, tail = self.verify(self.path)
            if not ok:
                raise RuntimeError("cadeia de evidencia coletada corrompida")
            self._n, self._tail = count, tail

    def append(self, record: dict) -> dict:
        with self._lock:
            entry = dict(record)
            entry.update(n=self._n + 1, prev=self._tail)
            entry["hash"] = line_hash(entry)
            with self.path.open("ab") as handle:
                handle.write(canonical(entry) + b"\n")
                handle.flush()
                if self._fsync:
                    os.fsync(handle.fileno())
            self._n, self._tail = self._n + 1, entry["hash"]
            return entry

    @staticmethod
    def verify(path):
        tail, count = GENESIS, 0
        for raw in Path(path).read_bytes().splitlines():
            if not raw.strip():
                continue
            record = json.loads(raw.decode("utf-8"))
            if record.get("prev") != tail or record.get("n") != count + 1 or record.get("hash") != line_hash(record):
                return False, count, tail
            tail, count = record["hash"], count + 1
        return True, count, tail

    @staticmethod
    def read(path) -> list:
        return [json.loads(raw.decode("utf-8")) for raw in Path(path).read_bytes().splitlines() if raw.strip()]
