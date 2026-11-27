"""Infra de teste: processo REAL do Gateway, cliente TLS cru, config TOML."""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sysconfig
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = Path(os.environ.get("AUNERON_GW_SRC") or (ROOT / "src")).resolve()
SERVER_NAME = "ai.auneron.internal"
PYEXE = getattr(sys, "_base_executable", sys.executable)

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


TLS_MODE = {"tls": True}  # conftest decide: False se o TLS em loopback e interceptado


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@dataclass
class Resp:
    status: int
    headers: dict[str, str]
    body: bytes
    raw: bytes

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def code(self) -> str | None:
        try:
            return self.json()["error"]["code"]
        except (ValueError, KeyError, TypeError):
            return None


def parse_response(buf: bytes) -> Resp | None:
    if not buf.startswith(b"HTTP/1.1 "):
        return None
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    hdrs = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        hdrs[k.strip().lower()] = v.strip()
    n = int(hdrs.get("content-length", "0"))
    return Resp(status, hdrs, rest[:n], buf)


def client_ctx(pair, ca, *, max_tls=None, min_tls=None, check_hostname=True) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = check_hostname
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cafile=str(ca))
    if pair is not None:
        ctx.load_cert_chain(str(pair.cert_path), str(pair.key_path))
    if max_tls == "1.2":
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if min_tls == "1.2":
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class Conn:
    """Conexao TLS crua: envia bytes, le respostas."""

    def __init__(self, gw: "Gateway", pair, *, ca=None, hostname=SERVER_NAME, max_tls=None, timeout=10.0):
        self.sock = socket.create_connection(("127.0.0.1", gw.port), timeout=5)
        if not gw.tls:
            self.sock.settimeout(timeout)
            self.buf = b""
            return
        ctx = client_ctx(pair, ca or gw.pki.ca_path, max_tls=max_tls)
        try:
            self.sock = ctx.wrap_socket(self.sock, server_hostname=hostname)
        except BaseException:
            self.sock.close()
            raise
        self.sock.settimeout(timeout)
        self.buf = b""

    def version(self) -> str:
        return self.sock.version()

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read_response(self) -> Resp | None:
        """Le uma resposta HTTP completa; None se fechou sem resposta."""
        try:
            while b"\r\n\r\n" not in self.buf:
                chunk = self.sock.recv(65536)
                if not chunk:
                    return None
                self.buf += chunk
            head, _, rest = self.buf.partition(b"\r\n\r\n")
            n = 0
            for ln in head.decode("latin-1").split("\r\n")[1:]:
                if ln.lower().startswith("content-length:"):
                    n = int(ln.split(":")[1])
            while len(rest) < n:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                rest += chunk
                self.buf += chunk
            consumed = len(head) + 4 + n
            raw, self.buf = self.buf[:consumed], self.buf[consumed:]
            return parse_response(raw)
        except (OSError, ValueError):
            return None

    def closed_by_peer(self, wait: float) -> bool:
        """True se o servidor fechou a conexao dentro de `wait` segundos."""
        self.sock.settimeout(wait)
        try:
            while True:
                if not self.sock.recv(65536):
                    return True
        except socket.timeout:
            return False
        except OSError:
            return True

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def build_request(
    method: str,
    target: str,
    body: bytes | None = b"",
    *,
    headers: dict[str, str] | None = None,
    content_type: str | None = "application/json",
    chunked: bool = False,
    content_length: int | None = None,
) -> bytes:
    h = [f"{method} {target} HTTP/1.1", f"Host: {SERVER_NAME}"]
    if content_type and body is not None:
        h.append(f"Content-Type: {content_type}")
    for k, v in (headers or {}).items():
        h.append(f"{k}: {v}")
    payload = b""
    if body is not None:
        if chunked:
            h.append("Transfer-Encoding: chunked")
            for i in range(0, len(body), 1000):
                part = body[i : i + 1000]
                payload += f"{len(part):x}\r\n".encode() + part + b"\r\n"
            payload += b"0\r\n\r\n"
        else:
            n = len(body) if content_length is None else content_length
            h.append(f"Content-Length: {n}")
            payload = body
    return ("\r\n".join(h) + "\r\n\r\n").encode("latin-1") + payload


def good_payload(request_id: str = "req-00000001", **over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "profile": "qwen3-4b-instruct-2507",
        "instructions": "Responda em JSON.",
        "input_text": "Texto sintetico de teste.",
        "output_schema": SCHEMA,
        "data_classification": "synthetic",
        "request_id": request_id,
    }
    d.update(over)
    return d


def dumps(obj: Any) -> bytes:
    return json.dumps(obj).encode("utf-8")


class Gateway:
    def __init__(self, pki, ollama_port: int, *, allowed=("client_ok", "client_ok2"), tls=None, **limits: Any):
        self.tls = TLS_MODE["tls"] if tls is None else tls
        self.pki = pki
        self.ollama_port = ollama_port
        self.allowed = allowed
        self.limits = limits
        self.port = free_port()
        self.tmp = Path(tempfile.mkdtemp(prefix="aigw-run-"))
        self.log_dir = self.tmp / "logs"
        self.stdout_path = self.tmp / "stdout.txt"
        self.stderr_path = self.tmp / "stderr.txt"
        self.proc: subprocess.Popen | None = None

    def config_text(self) -> str:
        lim = dict(
            handshake_timeout=3.0, header_timeout=3.0, body_timeout=3.0, idle_timeout=10.0,
            write_timeout=5.0, queue_size=2, per_minute=10, total_timeout=120.0, min_version="1.3",
            max_connections=4, host="127.0.0.1", enabled='"qwen3-4b-instruct-2507"', exposure='refuse',
        )
        lim.update(self.limits)
        fps = ", ".join(f'"{self.pki[n].fingerprint}"' for n in self.allowed)
        p = lambda x: str(x).replace("\\", "/")
        return f"""
[server]
host = "{lim['host']}"
port = {self.port}
max_connections = {lim['max_connections']}
handshake_timeout = {lim['handshake_timeout']}
header_timeout = {lim['header_timeout']}
body_timeout = {lim['body_timeout']}
idle_timeout = {lim['idle_timeout']}
write_timeout = {lim['write_timeout']}
[tls]
ca_file = "{p(self.pki.ca_path)}"
cert_file = "{p(self.pki['server'].cert_path)}"
key_file = "{p(self.pki['server'].key_path)}"
min_version = "{lim['min_version']}"
allowed_client_fingerprints = [{fps}]
[ollama]
port = {self.ollama_port}
[limits]
queue_size = {lim['queue_size']}
per_minute = {lim['per_minute']}
total_timeout = {lim['total_timeout']}
[profiles]
enabled = [{lim['enabled']}]
[startup]
exposure_unverifiable = "{lim['exposure']}"
[logging]
dir = "{p(self.log_dir)}"
retention_days = 30
"""

    def write_config(self) -> Path:
        path = self.tmp / "gateway.toml"
        path.write_text(self.config_text(), encoding="utf-8")
        return path

    def spawn(self) -> subprocess.Popen:
        cfg = self.write_config()
        env = {k: v for k, v in os.environ.items() if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY")}
        # o python.exe do venv e um lancador que cria processo filho; usa o interpretador base
        # direto (PID real, sem orfaos) e aponta para o site-packages do venv.
        env["PYTHONPATH"] = os.pathsep.join([str(SRC), sysconfig.get_paths()["purelib"]])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        out = open(self.stdout_path, "ab")
        err = open(self.stderr_path, "ab")
        self.proc = subprocess.Popen(
            [PYEXE, "-m", "auneron_ai_gateway", "--config", str(cfg)]
            if self.tls
            else [PYEXE, str(HERE / "plain_launcher.py"), str(cfg)],
            stdout=out, stderr=err, cwd=str(self.tmp), env=env,
        )
        out.close()
        err.close()
        return self.proc

    def start(self, wait: bool = True) -> "Gateway":
        self.spawn()
        if wait:
            self.wait_ready()
        return self

    def wait_ready(self, timeout: float = 20.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"gateway saiu: {self.proc.returncode}: {self.stderr_path.read_text()[:300]}")
            if b"READY" in self.stdout_path.read_bytes():
                return
            time.sleep(0.05)
        raise RuntimeError("gateway nao ficou pronto")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)

    def restart(self) -> None:
        self.stop()
        self.stdout_path.write_bytes(b"")
        self.start()

    # ---------------------------------------------------------------- uso
    def conn(self, name: str = "client_ok", **kw: Any) -> Conn:
        return Conn(self, self.pki[name] if name else None, **kw)

    def attempt(self, name: str | None, raw: bytes, **kw: Any) -> Resp | None:
        """Tenta uma requisicao; None = recusado (handshake ou fechamento sem resposta HTTP).

        Roda num thread proprio: a fila de erros do OpenSSL e por thread, e o erro de uma tentativa
        recusada (alerta TLS) ficava nela e contaminava a leitura seguinte de OUTRA conexao do mesmo
        thread no cliente de teste (Python 3.11.18 + OpenSSL 3.5.9; achado no container, 27/11/2026).
        O servidor nao e afetado: recebeu e respondeu normalmente."""
        box: list[Any] = [None, None]

        def run() -> None:
            try:
                box[0] = self._attempt(name, raw, **kw)
            except BaseException as exc:  # erro inesperado nao pode virar "recusado"
                box[1] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join()
        if box[1] is not None:
            raise box[1]
        return box[0]

    def _attempt(self, name: str | None, raw: bytes, **kw: Any) -> Resp | None:
        try:
            c = Conn(self, self.pki[name] if name else None, **kw)
        except (OSError, ssl.SSLError):
            return None
        try:
            c.send(raw)
            return c.read_response()
        except OSError:
            return None
        finally:
            c.close()

    def request(self, raw: bytes, name: str = "client_ok", **kw: Any) -> Resp:
        r = self.attempt(name, raw, **kw)
        assert r is not None, "sem resposta HTTP"
        return r

    def post(self, payload: Any, name: str = "client_ok", **kw: Any) -> Resp:
        body = payload if isinstance(payload, bytes) else dumps(payload)
        return self.request(build_request("POST", "/v1/generate", body), name, **kw)

    def get(self, path: str = "/v1/health", name: str = "client_ok") -> Resp:
        return self.request(build_request("GET", path, None), name)

    def log_records(self) -> list[dict[str, Any]]:
        out = []
        for f in sorted(self.log_dir.glob("gateway.log*")):
            for ln in f.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    out.append(json.loads(ln))
        return out

    def all_files(self) -> list[Path]:
        return [p for p in self.tmp.rglob("*") if p.is_file()]

    def cleanup(self) -> None:
        self.stop()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

