"""INTELLIGENCE-IMPL-0 -- isolation boundaries of the foundation.

Static (AST) and runtime checks that the gateway cannot reach the
network, read provider credentials, expose a route/permission, or be
imported by the application. Each scanner is also exercised on a
synthetic violating snippet so a silent scanner failure cannot pass.
"""

import ast
import re
import socket
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.database.database import Base
from app.database.database import SessionLocal


pytestmark = pytest.mark.security

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
GATEWAY_DIR = APP / "intelligence"
MIGRATION = (
    BACKEND
    / "migrations"
    / "versions"
    / "a7d3e91c4b26_add_intelligence_budget_ledger.py"
)

FORBIDDEN_IMPORT_ROOTS = {
    "requests", "httpx", "httpx2", "aiohttp", "urllib", "urllib3", "http",
    "socket", "ssl", "websockets", "websocket", "smtplib", "ftplib",
    "telnetlib", "xmlrpc", "subprocess", "multiprocessing", "ctypes",
    "openai", "anthropic", "ollama", "litellm", "langchain", "google",
    "boto3", "grpc", "paramiko", "tornado", "importlib",
}
FORBIDDEN_APP_PREFIXES = (
    "app.main",
    "app.api",
    "app.services",
    "app.agents",
    "app.orchestrator",
    "app.core.authentication",
    "app.core.authorization",
    "app.core.security",
    "app.core.approval",
    "app.core.work",
)
IMPORTS_GATEWAY = re.compile(
    r"(from\s+app\.intelligence|import\s+app\.intelligence|"
    r"from\s+app\s+import\s+intelligence)"
)
FORBIDDEN_CALLS = {"eval", "exec", "__import__", "compile", "open"}
ALLOWED_ENV_KEYS = {"APP_ENV"}

APP_FILES_ALLOWED_TO_MENTION_INTELLIGENCE = {
    "app/core/config.py",
    "app/core/observability.py",
    "app/models/__init__.py",
    "app/models/intelligence_budget.py",
    "app/repositories/intelligence_budget_repository.py",
}


def python_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def scan_source(source: str) -> list[str]:
    """Returns violations found in a module's source."""
    problems: list[str] = []
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []

        for name in names:
            if name.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                problems.append(f"forbidden import {name}")

            if name.startswith(FORBIDDEN_APP_PREFIXES):
                problems.append(f"forbidden application import {name}")

        if isinstance(node, ast.Call):
            func = node.func

            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                problems.append(f"forbidden call {func.id}()")

            chain = []
            current = func

            while isinstance(current, ast.Attribute):
                chain.append(current.attr)
                current = current.value

            if isinstance(current, ast.Name):
                chain.append(current.id)

            dotted = ".".join(reversed(chain))

            if dotted in ("os.getenv", "os.environ.get", "os.putenv"):
                first = node.args[0] if node.args else None

                if not (
                    isinstance(first, ast.Constant)
                    and first.value in ALLOWED_ENV_KEYS
                ):
                    problems.append(f"environment read {dotted}(...)")

            if dotted in ("os.system", "os.popen", "os.exec", "os.spawn"):
                problems.append(f"process execution {dotted}")

        if isinstance(node, ast.Subscript):
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "environ"
                and isinstance(value.value, ast.Name)
                and value.value.id == "os"
            ):
                key = node.slice
                if not (
                    isinstance(key, ast.Constant)
                    and key.value in ALLOWED_ENV_KEYS
                ):
                    problems.append("environment subscript read")

    return problems


# ----------------------------------------------------------------------
# static isolation of app/intelligence
# ----------------------------------------------------------------------


GATEWAY_FILES = [
    "gateway/__init__.py",
    "gateway/adapters/__init__.py",
    "gateway/adapters/base.py",
    "gateway/adapters/fake.py",
    "gateway/budget.py",
    "gateway/circuit_breaker.py",
    "gateway/concurrency.py",
    "gateway/contracts.py",
    "gateway/errors.py",
    "gateway/gateway.py",
    "gateway/pricing.py",
    "gateway/profiles.py",
    "gateway/redaction.py",
]
PLANNING_FILES = [
    "planning/__init__.py",
    "planning/admission.py",
    "planning/authority.py",
    "planning/canonical.py",
    "planning/capabilities.py",
    "planning/codes.py",
    "planning/dag.py",
    "planning/limits.py",
    "planning/policy.py",
    "planning/schema.py",
    "planning/validator.py",
]


def test_gateway_modules_import_nothing_that_can_reach_the_network() -> None:
    files = python_files(GATEWAY_DIR)
    assert sorted(p.relative_to(GATEWAY_DIR).as_posix() for p in files) == sorted(
        ["__init__.py"] + GATEWAY_FILES + PLANNING_FILES
    )  # exactly the approved file sets; no real adapter module exists

    violations = {
        str(path.relative_to(BACKEND)): scan_source(
            path.read_text(encoding="utf-8")
        )
        for path in files
    }
    violations = {k: v for k, v in violations.items() if v}

    assert violations == {}


@pytest.mark.parametrize(
    "snippet, expected",
    [
        ("import httpx", "forbidden import httpx"),
        ("from urllib.request import urlopen", "forbidden import urllib.request"),
        ("import socket", "forbidden import socket"),
        ("import openai", "forbidden import openai"),
        ("from anthropic import Anthropic", "forbidden import anthropic"),
        ("import subprocess", "forbidden import subprocess"),
        ("from app.main import app", "forbidden application import app.main"),
        (
            "from app.services.approval_service import ApprovalService",
            "forbidden application import app.services.approval_service",
        ),
        (
            "from app.core.authentication import require_permission",
            "forbidden application import app.core.authentication",
        ),
        ("import os\nx = os.environ['OPENAI_API_KEY']", "environment subscript read"),
        ("import os\nx = os.getenv('ANTHROPIC_API_KEY')", "environment read os.getenv(...)"),
        ("eval('1')", "forbidden call eval()"),
        ("import os\nos.system('curl x')", "process execution os.system"),
    ],
)
def test_sensitivity_scanner_flags_synthetic_violations(snippet, expected) -> None:
    assert expected in scan_source(snippet)


def test_scanner_allows_the_single_sanctioned_environment_read() -> None:
    assert scan_source("import os\nos.environ.get('APP_ENV', '')") == []


def test_only_app_env_is_read_from_the_environment() -> None:
    reads = []

    for path in python_files(GATEWAY_DIR):
        for match in re.finditer(
            r"os\.(?:environ|getenv)[^\n]*", path.read_text(encoding="utf-8")
        ):
            reads.append((path.name, match.group(0)))

    assert reads and all("APP_ENV" in line for _, line in reads)
    assert {name for name, _ in reads} == {"profiles.py"}


def test_no_credential_or_provider_names_are_referenced_by_the_gateway() -> None:
    pattern = re.compile(
        r"(api[_-]?key|bearer|authorization|openai|anthropic|ollama|"
        r"sk-[A-Za-z0-9]+|secret|password)",
        re.IGNORECASE,
    )
    hits = []

    for path in python_files(GATEWAY_DIR):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            code = line.split("#", 1)[0]

            if pattern.search(code):
                hits.append(f"{path.name}:{number}: {line.strip()}")

    assert hits == []


# ----------------------------------------------------------------------
# the rest of the application does not know the gateway exists
# ----------------------------------------------------------------------


def test_no_application_module_imports_the_gateway() -> None:
    offenders = []

    for path in python_files(APP):
        relative = path.relative_to(BACKEND).as_posix()

        if relative.startswith("app/intelligence/"):
            continue

        text = path.read_text(encoding="utf-8")

        if (
            IMPORTS_GATEWAY.search(text)
            and relative not in APP_FILES_ALLOWED_TO_MENTION_INTELLIGENCE
        ):
            offenders.append(relative)

    assert offenders == []


def test_main_module_and_routers_do_not_reference_intelligence() -> None:
    sources = [APP / "main.py"] + python_files(APP / "api")

    assert len(sources) > 5
    for path in sources:
        assert not IMPORTS_GATEWAY.search(path.read_text(encoding="utf-8")), path


def test_no_route_is_registered_for_the_gateway() -> None:
    from app.main import app

    suspicious = []

    for route in app.routes:
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        module = getattr(endpoint, "__module__", "") or ""
        name = getattr(endpoint, "__name__", "") or ""
        label = f"{path} {module} {name}".lower()

        if any(token in label for token in ("intelligence", "/llm", "gateway")):
            suspicious.append(label)

    assert suspicious == []
    assert len(app.routes) > 10  # the inspection saw the real app


def test_no_permission_or_role_mentions_the_gateway() -> None:
    for relative in (
        "app/core/authorization.py",
        "app/core/authentication.py",
        "app/core/approval_authorization.py",
        "app/core/work_authorization.py",
        "app/core/skill_authorization.py",
        "app/core/memory_authorization.py",
        "app/models/user.py",
    ):
        source = (BACKEND / relative).read_text(encoding="utf-8")
        assert not IMPORTS_GATEWAY.search(source), relative
        assert "intelligence." not in source.lower(), relative


# ----------------------------------------------------------------------
# schema and migration scope
# ----------------------------------------------------------------------


def test_only_the_two_sanctioned_tables_were_added_to_metadata() -> None:
    import app.models  # noqa: F401 - registers every model

    mine = sorted(name for name in Base.metadata.tables if name.startswith("intelligence"))

    assert mine == ["intelligence_budgets", "intelligence_model_calls"]


def test_migration_is_one_additive_revision_on_the_expected_parent() -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    script = ScriptDirectory.from_config(config)

    assert len(script.get_heads()) == 1
    revision = script.get_revision("a7d3e91c4b26")
    assert revision.down_revision == "5c1e7a90d2b4"
    assert script.get_heads() == ["a7d3e91c4b26"]

    source = MIGRATION.read_text(encoding="utf-8")
    operations = set(re.findall(r"op\.(\w+)\(", source))
    tables = set(re.findall(r'_(?:BUDGETS|CALLS)\s*=\s*"(\w+)"', source))

    assert operations == {"create_table", "create_index", "drop_index", "drop_table"}
    assert tables == {"intelligence_budgets", "intelligence_model_calls"}
    assert "op.execute" not in source
    assert "ALTER" not in source.upper().replace("ALTERNATIVE", "")


# ----------------------------------------------------------------------
# runtime: a full call opens no network path
# ----------------------------------------------------------------------


def test_full_gateway_call_attempts_no_network_access(monkeypatch) -> None:
    from app.intelligence.gateway.adapters.fake import FakeProvider
    from app.intelligence.gateway.adapters.fake import ok
    from app.intelligence.gateway.budget import BudgetLedger
    from app.intelligence.gateway.budget import utcnow
    from app.intelligence.gateway.contracts import ModelCallRequest
    from app.intelligence.gateway.gateway import Gateway
    from app.intelligence.gateway.gateway import GatewayLimits
    from app.intelligence.gateway.profiles import build_lab_registry

    attempts = []

    def deny(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("network access attempted")

    ledger = BudgetLedger(
        SessionLocal, unknown_fraction_bps=2500, recovery_grace_seconds=30
    )
    now = utcnow()
    budget_id = ledger.create_budget(
        scope_kind="deployment_period",
        scope_ref="dep",
        limit_micro=10_000_000,
        limit_tokens=10_000_000,
        period_start=now - timedelta(hours=1),
        period_end=now + timedelta(hours=1),
    )
    gateway = Gateway(
        registry=build_lab_registry(),
        adapters={"fake": FakeProvider([ok({"ok": True})])},
        ledger=ledger,
        limits=GatewayLimits(2, 5, 2000),
        mode_provider=lambda: "shadow",
    )

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    import http.client
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", deny)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", deny)

    result = gateway.call(
        ModelCallRequest(
            call_key="net-1",
            purpose="lab.boundary",
            profile="lab-fast",
            instructions="i",
            input_text="t",
            output_schema={"type": "object"},
            max_output_tokens=16,
            deployment_budget_id=budget_id,
        )
    )

    assert result.ok and attempts == []


def test_documentation_set_exists() -> None:
    for name in ("README.md", "GATEWAY_CONTRACT.md", "BUDGET_LEDGER.md"):
        path = BACKEND / "docs" / "intelligence" / name
        assert path.is_file() and path.stat().st_size > 500
