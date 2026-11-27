"""Item 8 do plano: fronteira. Varredura AST de imports, rotas e hosts; sem backend, sem banco."""

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from harness import PYEXE, SRC

PKG = SRC / "auneron_ai_gateway"
FILES = sorted(PKG.glob("*.py"))

STDLIB_OK = {
    "__future__", "asyncio", "collections", "dataclasses", "hashlib", "hmac", "http", "ipaddress",
    "json", "logging", "math", "pathlib", "re", "socket", "ssl", "time", "tomllib", "typing", "argparse",
    "sys",
}
THIRD_OK = {"pydantic"}
# extras por arquivo (superficie minima de cada modulo)
PER_FILE_EXTRA = {
    "fsperm.py": {"os", "stat", "ctypes"},
    "exposure.py": {"os", "ctypes", "struct"},
    "ollama.py": {"concurrent"},
    "certinfo.py": {"calendar"},
    "server.py": {"uvicorn"},
    "netbind.py": set(),
}
FORBIDDEN_ANYWHERE = {
    "backend", "sqlalchemy", "psycopg", "psycopg2", "sqlite3", "asyncpg", "alembic", "requests", "httpx",
    "urllib", "urllib3", "aiohttp", "subprocess", "shutil", "pickle", "multiprocessing",
    "socketserver", "ftplib", "smtplib", "telnetlib", "xmlrpc", "webbrowser", "importlib", "fastapi", "starlette",
    "websockets", "wsproto", "httptools", "uvloop",
}


def imports_of(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name.split(".")[0], node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relativo ao proprio pacote
            out.append(((node.module or "").split(".")[0], node.lineno))
    return out


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_imports_allowlist(path):
    for mod, line in imports_of(path):
        assert mod not in FORBIDDEN_ANYWHERE, f"{path.name}:{line} importa {mod}"
        assert mod in STDLIB_OK | THIRD_OK | PER_FILE_EXTRA.get(path.name, set()), (
            f"{path.name}:{line} import fora da lista: {mod}")


def test_no_database_or_backend_names_anywhere_in_package():
    for f in FILES:
        text = f.read_text(encoding="utf-8").lower()
        for bad in ("import backend", "from backend", "sqlalchemy", "psycopg", "sqlite", "database_url"):
            assert bad not in text, (f.name, bad)


def test_dynamic_code_and_shell_not_used():
    banned_names = {"eval", "exec", "compile", "__import__"}
    banned_attrs = {"system", "popen", "Popen", "check_output", "startfile"}
    for f in FILES:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    assert fn.id not in banned_names, (f.name, fn.id, node.lineno)
                elif isinstance(fn, ast.Attribute):
                    assert fn.attr not in banned_attrs, (f.name, fn.attr, node.lineno)


def test_http_client_module_only_in_reference_client_and_ollama_client():
    users = [f.name for f in FILES if any(m == "http" for m, _ in imports_of(f))]
    assert sorted(users) == ["client.py", "ollama.py"]  # http.client; nenhum parser HTTP proprio
    assert not (PKG / "httpwire.py").exists()
    for f in FILES:
        text = f.read_text(encoding="utf-8")
        assert "readuntil(" not in text and "def parse_request_head" not in text, f.name


def _string_constants(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                doc_nodes.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes:
            yield node.value, node.lineno


def test_route_literals_are_exactly_the_five_allowed():
    allowed = {"/v1/generate", "/v1/health", "/api/version", "/api/tags", "/api/chat"}
    found = set()
    for f in FILES:
        for s, line in _string_constants(f):
            if s.startswith("/") and "\n" not in s and " " not in s and not s.startswith("/proc/net/tcp"):
                found.add(s)
    assert found == allowed, found ^ allowed


def test_declared_routes_and_ollama_calls_tables():
    from auneron_ai_gateway import app, ollama, server

    assert app.ROUTES == server.ROUTES
    assert server.ROUTES == {"/v1/generate": "POST", "/v1/health": "GET"}
    assert ollama.ALLOWED_CALLS == {("GET", "/api/version"), ("GET", "/api/tags"), ("POST", "/api/chat")}


def test_host_literals_are_loopback_or_the_fixed_server_name_only():
    host_like = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|localhost|::1?|[a-z0-9-]+(\.[a-z0-9-]+)+)$", re.I)
    allowed = {"127.0.0.1", "ai.auneron.internal"}
    seen = set()
    for f in FILES:
        for s, line in _string_constants(f):
            is_version = re.match(r"^\d+(\.\d+)?(\.\d+)?(-[a-z0-9]+)?$", s) and not re.match(r"^\d+(\.\d+){3}$", s)
            is_cfg_key = s.startswith(("server.", "tls.", "limits.", "logging.", "ollama.", "profiles.", "startup.", "http."))
            if host_like.match(s) and not is_version and not is_cfg_key and not s.endswith((".py", ".log", ".toml", ".pem")):
                seen.add(s)
                assert s in allowed, f"{f.name}:{line} host inesperado {s}"
    assert "127.0.0.1" in seen
    for f in FILES:
        for s, line in _string_constants(f):
            assert s not in ("0.0.0.0", "::", "localhost", "*"), (f.name, line, s)
        assert "INADDR_ANY" not in f.read_text(encoding="utf-8")


def test_no_listen_or_connect_call_uses_non_loopback_target():
    """Todo bind/connect usa cfg.host (so 127.0.0.1) ou OLLAMA_HOST; sem literais de outro destino."""
    nb = (PKG / "netbind.py").read_text(encoding="utf-8")
    assert "sock.bind((host, port))" in nb and "SO_REUSEADDR" not in nb and "SO_EXCLUSIVEADDRUSE" in nb
    assert "bind_exclusive(self.cfg.host, self.cfg.port)" in (PKG / "server.py").read_text(encoding="utf-8")
    oll = (PKG / "ollama.py").read_text(encoding="utf-8")
    assert "socket.create_connection((OLLAMA_HOST, self.port)" in oll


def test_importing_the_package_does_not_load_backend_or_db_modules():
    code = (
        "import sys, auneron_ai_gateway.server, auneron_ai_gateway.client, auneron_ai_gateway.__main__;"
        "bad=[m for m in sys.modules if m.split('.')[0] in "
        "('backend','sqlalchemy','psycopg','psycopg2','sqlite3','requests','httpx','fastapi','starlette','websockets','httptools','uvloop','wsproto')];"
        "print(bad)"
    )
    import os, sysconfig

    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), sysconfig.get_paths()["purelib"]]))
    r = subprocess.run([PYEXE, "-c", code], capture_output=True, text=True, env=env, timeout=30, cwd=str(PKG.parent))
    assert r.returncode == 0 and r.stdout.strip() == "[]", r.stdout + r.stderr


def test_dependencies_are_pinned_with_hashes():
    root = Path(__file__).resolve().parent.parent
    for name in ("requirements.txt", "requirements-dev.txt"):
        text = (root / name).read_text(encoding="utf-8")
        reqs = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", text, re.M)
        assert reqs
        assert text.count("--hash=sha256:") >= len(reqs)
        assert not re.search(r"^[A-Za-z0-9_.-]+\s*(>=|<=|~=|>|<)", text, re.M)
