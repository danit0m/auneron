"""INTELLIGENCE-PLANNING -- isolation boundaries of the planning package.

Static (AST) checks that the planning package is pure, offline and
inert: it imports no provider, network, persistence, route, service or
execution module, defines no executor, and the rest of the application
does not know it exists.
"""

import ast
import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.database.database import Base

from test_intelligence_boundaries import PLANNING_FILES
from test_intelligence_boundaries import python_files
from test_intelligence_boundaries import scan_source


pytestmark = pytest.mark.security

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
PLANNING_DIR = APP / "intelligence" / "planning"

ALLOWED_THIRD_PARTY = {"jsonschema"}
ALLOWED_STDLIB = {
    "collections",
    "dataclasses",
    "datetime",  # calendar validation only (date.fromisoformat); no clock reads
    "enum",
    "hashlib",
    "json",
    "re",
    "typing",
    "unicodedata",
}
ALLOWED_APP_PREFIXES = (
    "app.intelligence.planning.",
    "app.intelligence.gateway.profiles",
    "app.intelligence.gateway.contracts",
)
NON_DETERMINISTIC_OR_IO = {
    "random", "secrets", "time", "uuid", "os", "sys", "pathlib",
    "shutil", "tempfile", "logging", "threading", "asyncio", "pickle",
    "sqlalchemy", "fastapi", "pydantic", "alembic",
}
EXECUTION_WORDS = re.compile(
    r"(execute|dispatch|enqueue|schedule|approve|submit|invoke|run_plan|apply)",
    re.IGNORECASE,
)


def imported_modules(source: str):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def disallowed_imports(source: str) -> list[str]:
    problems = []

    for name in imported_modules(source):
        root = name.split(".")[0]

        if root == "app":
            if not name.startswith(ALLOWED_APP_PREFIXES):
                problems.append(name)
        elif root not in ALLOWED_THIRD_PARTY | ALLOWED_STDLIB:
            problems.append(name)

    return problems


def test_the_planning_package_is_exactly_the_approved_file_set():
    found = sorted(
        p.relative_to(APP / "intelligence").as_posix() for p in python_files(PLANNING_DIR)
    )

    assert found == sorted(PLANNING_FILES)
    assert len(found) == 11
    assert (PLANNING_DIR / "__init__.py").read_text(encoding="utf-8").strip() == ""


def test_planning_imports_only_the_allow_list():
    offenders = {
        p.name: disallowed_imports(p.read_text(encoding="utf-8"))
        for p in python_files(PLANNING_DIR)
    }

    assert {k: v for k, v in offenders.items() if v} == {}


def test_planning_passes_the_gateway_static_scanner():
    for path in python_files(PLANNING_DIR):
        assert scan_source(path.read_text(encoding="utf-8")) == [], path.name


def test_planning_has_no_clock_randomness_io_or_persistence():
    for path in python_files(PLANNING_DIR):
        roots = {m.split(".")[0] for m in imported_modules(path.read_text(encoding="utf-8"))}

        assert not roots & NON_DETERMINISTIC_OR_IO, (path.name, roots & NON_DETERMINISTIC_OR_IO)


CLOCK_ATTRIBUTES = {"now", "today", "utcnow", "monotonic", "perf_counter", "time", "sleep"}


def clock_reads(source: str) -> list[str]:
    return [
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and node.attr in CLOCK_ATTRIBUTES
    ]


def test_planning_reads_no_clock():
    for path in python_files(PLANNING_DIR):
        assert clock_reads(path.read_text(encoding="utf-8")) == [], path.name

    assert clock_reads("import datetime\ndatetime.datetime.now()") == ["now"]
    assert clock_reads("from datetime import date\ndate.today()") == ["today"]
    assert clock_reads("from datetime import date\ndate.fromisoformat('x')") == []


def test_planning_never_touches_the_existing_governance_or_persistence_layers():
    forbidden = (
        "app.core",
        "app.models",
        "app.services",
        "app.repositories",
        "app.api",
        "app.database",
        "app.main",
        "app.schemas",
        "app.agents",
        "app.orchestrator",
    )

    for path in python_files(PLANNING_DIR):
        for name in imported_modules(path.read_text(encoding="utf-8")):
            assert not name.startswith(forbidden), (path.name, name)


def test_planning_imports_from_the_gateway_are_the_two_declared_ones():
    seen = set()

    for path in python_files(PLANNING_DIR):
        for name in imported_modules(path.read_text(encoding="utf-8")):
            if name.startswith("app.intelligence.gateway"):
                seen.add(name)

    assert seen == {
        "app.intelligence.gateway.contracts",
        "app.intelligence.gateway.profiles",
    }


def test_there_is_no_executor_or_dispatch_surface():
    offenders = []

    for path in python_files(PLANNING_DIR):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if EXECUTION_WORDS.search(node.name):
                    offenders.append(f"{path.name}:{node.name}")

            if isinstance(node, ast.AsyncFunctionDef):
                offenders.append(f"{path.name}:async {node.name}")

    assert offenders == []


def test_every_capability_and_the_flag_are_inert():
    from app.intelligence.planning import capabilities as caps

    assert caps.EXECUTION_ENABLED_IN_THIS_PHASE is False
    assert not any(d.executable for d in caps.REGISTRY_V1.definitions)
    source = (PLANNING_DIR / "capabilities.py").read_text(encoding="utf-8")
    assert "EXECUTION_ENABLED_IN_THIS_PHASE: Final[bool] = False" in source


# The proposal package is the one consumer of the planning package that is
# allowed to import it. The exemption is that exact directory: the
# gateway, the planning package itself (handled separately) and everything
# outside app/intelligence stay covered by the scan.
PLANNING_IMPORT = re.compile(
    r"intelligence\.planning|intelligence\s+import\s+planning"
)
PLANNING_IMPORT_EXEMPT = ("app/intelligence/planning/", "app/intelligence/proposal/")


def planning_import_offenders(sources):
    """sources: {path relative to backend, file text}."""
    return sorted(
        relative
        for relative, text in sources.items()
        if not relative.startswith(PLANNING_IMPORT_EXEMPT)
        and PLANNING_IMPORT.search(text)
    )


def test_the_application_does_not_import_the_planning_package():
    sources = {
        path.relative_to(BACKEND).as_posix(): path.read_text(encoding="utf-8")
        for path in python_files(APP)
    }

    assert any(name.startswith("app/intelligence/proposal/") for name in sources)
    assert planning_import_offenders(sources) == []


def test_the_planning_import_scan_still_detects_everything_but_proposal():
    statement = "from app.intelligence.planning.validator import validate_plan"
    spelled = "from app.intelligence import planning"
    sources = {
        "app/services/x.py": statement,
        "app/api/routes/y.py": spelled,
        "app/core/z.py": "import app.intelligence.planning.capabilities",
        "app/main.py": statement,
        "app/intelligence/gateway/gateway.py": statement,
        "app/intelligence/other/new_module.py": statement,
        "app/intelligence/__init__.py": spelled,
        "app/intelligence/proposal/builder.py": statement,
        "app/intelligence/planning/validator.py": statement,
        "app/services/clean.py": "from app.intelligence.gateway.gateway import Gateway",
    }

    assert planning_import_offenders(sources) == [
        "app/api/routes/y.py",
        "app/core/z.py",
        "app/intelligence/__init__.py",
        "app/intelligence/gateway/gateway.py",
        "app/intelligence/other/new_module.py",
        "app/main.py",
        "app/services/x.py",
    ]
    # a lookalike directory name is not exempt
    assert planning_import_offenders(
        {
            "app/intelligence/proposals/x.py": statement,
            "app/intelligence/proposal_x/y.py": statement,
        }
    ) == ["app/intelligence/proposal_x/y.py", "app/intelligence/proposals/x.py"]



def test_the_reverse_direction_stays_closed():
    for directory in ("gateway", "planning"):
        for path in python_files(APP / "intelligence" / directory):
            assert "intelligence.proposal" not in path.read_text(encoding="utf-8"), path.name


def test_no_route_permission_table_or_migration_was_added():
    from app.main import app

    for route in app.routes:
        label = f"{getattr(route, 'path', '')} {getattr(getattr(route, 'endpoint', None), '__module__', '')}".lower()
        assert "planning" not in label and "/plan" not in label, label

    for relative in (
        "app/core/authorization.py",
        "app/core/authentication.py",
        "app/core/approval_authorization.py",
        "app/core/work_authorization.py",
        "app/core/skill_authorization.py",
        "app/core/memory_authorization.py",
    ):
        source = (BACKEND / relative).read_text(encoding="utf-8").lower()
        assert "planning" not in source, relative

    import app.models  # noqa: F401

    assert sorted(n for n in Base.metadata.tables if n.startswith("intelligence")) == [
        "intelligence_budgets",
        "intelligence_model_calls",
    ]
    assert not any("plan" in name for name in Base.metadata.tables if name.startswith("intelligence"))

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == ["a7d3e91c4b26"]


def test_the_credential_and_provider_vocabulary_stays_out_of_planning():
    pattern = re.compile(
        r"(api[_-]?key|bearer|authorization|openai|anthropic|ollama|sk-[A-Za-z0-9]+|secret|password)",
        re.IGNORECASE,
    )
    hits = []

    for path in python_files(PLANNING_DIR):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line.split("#", 1)[0]):
                hits.append(f"{path.name}:{number}")

    assert hits == []


@pytest.mark.parametrize(
    "snippet",
    [
        "import requests",
        "import random",
        "import time",
        "from app.core.autonomy_policy import evaluate_skill_autonomy",
        "from app.services.approval_service import ApprovalService",
        "from sqlalchemy.orm import Session",
        "import os",
        "from app.intelligence.gateway.gateway import Gateway",
    ],
)
def test_the_import_scanner_flags_synthetic_violations(snippet):
    assert disallowed_imports(snippet) != []


def test_the_scanner_accepts_the_sanctioned_imports():
    ok = (
        "import hashlib\nimport json\nfrom jsonschema import Draft202012Validator\n"
        "from app.intelligence.gateway.profiles import is_production_environment\n"
        "from app.intelligence.planning.codes import RejectionCode\n"
    )

    assert disallowed_imports(ok) == []


def test_planning_documentation_exists_and_states_the_boundary():
    path = BACKEND / "docs" / "intelligence" / "PLANNING_CONTRACT.md"
    text = path.read_text(encoding="utf-8")

    assert path.stat().st_size > 2000
    for required in (
        "plan.v1",
        "capreg.v1",
        "not an authorisation",
        "eligibility_verified",
        "no executor",
    ):
        assert required.lower() in text.lower(), required
