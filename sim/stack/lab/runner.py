"""Execucao de comandos sem shell, com saida capturada e segredos mascarados."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


class Runner:
    def __init__(self, secrets: list[str] | None = None) -> None:
        self.secrets = [s for s in (secrets or []) if s]

    def mask(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text

    def run(self, args: list[str], *, input_text: str | None = None, env: dict | None = None,
            cwd=None, timeout: int = 1800) -> Result:
        # stdin em BYTES: o modo texto no Windows traduz "\n" -> "\r\n" e a
        # senha lida por getpass no container ficaria com "\r" no final.
        data = None if input_text is None else input_text.encode("utf-8")
        proc = subprocess.run(args, input=data, env=env, cwd=cwd, capture_output=True, timeout=timeout)
        out = (proc.stdout or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
        err = (proc.stderr or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
        return Result(proc.returncode, self.mask(out), self.mask(err))
