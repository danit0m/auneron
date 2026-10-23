"""INTELLIGENCE-PROPOSAL -- isolation boundaries of the projection package.

Static (AST) checks that app/intelligence/proposal/ is pure, offline and
inert: it imports no gateway, validator, admission, provider, network,
persistence, route, service or execution module, defines no executor,
changes nothing it imports, and nothing else depends on it.
"""

import ast
import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.database.database import Base

from test_intelligence_boundaries import PROPOSAL_FILES
from test_intelligence_boundaries import python_files
from test_intelligence_boundaries import scan_source
from test_intelligence_plan_boundaries import EXECUTION_WORDS
from test_intelligence_plan_boundaries import imported_modules


pytestmark = pytest.mark.security

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
PROPOSAL_DIR = APP / "intelligence" / "proposal"

ALLOWED_STDLIB = {"collections", "copy", "dataclasses", "enum", "json", "re", "types", "typing"}
ALLOWED_THIRD_PARTY = {"jsonschema"}
ALLOWED_PLANNING = {
    "app.intelligence.planning.authority",
    "app.intelligence.planning.canonical",
    "app.intelligence.planning.capabilities",
    "app.intelligence.planning.limits",
    "app.intelligence.planning.policy",
    "app.intelligence.planning.schema",
}
FORBIDDEN_ROOTS = {
    "random", "secrets", "time", "datetime", "uuid", "os", "sys", "pathlib", "shutil",
    "tempfile", "logging", "threading", "asyncio", "pickle", "subprocess", "socket",
    "sqlalchemy", "fastapi", "pydantic", "alembic", "hashlib", "unicodedata",
}
CREDENTIAL_WORDS = re.compile(
    r"(api[_-]?key|bearer|authorization|openai|anthropic|ollama|sk-[A-Za-z0-9]+|secret|password)",
    re.IGNORECASE,
)


def import_problems(source):
    problems = []

    for name in imported_modules(source):
        root = name.split(".")[0]

        if root == "app":
            if not (
                name in ALLOWED_PLANNING
                or name.startswith("app.intelligence.proposal.")
            ):
                problems.append(name)
        elif root not in ALLOWED_STDLIB | ALLOWED_THIRD_PARTY:
            problems.append(name)

    return problems


def sources():
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in python_files(PROPOSAL_DIR)
    }


def test_the_package_is_exactly_the_approved_file_set():
    found = sorted(
        path.relative_to(APP / "intelligence").as_posix()
        for path in python_files(PROPOSAL_DIR)
    )

    assert found == sorted(PROPOSAL_FILES) and len(found) == 6
    assert (PROPOSAL_DIR / "__init__.py").read_text(encoding="utf-8").strip() == ""


def test_imports_are_limited_to_the_allow_list():
    offenders = {name: import_problems(text) for name, text in sources().items()}

    assert {name: found for name, found in offenders.items() if found} == {}


def test_the_projection_never_reaches_the_gateway_the_validator_or_the_admission():
    for name, text in sources().items():
        modules = list(imported_modules(text))

        assert not [m for m in modules if m.startswith("app.intelligence.gateway")], name
        assert "app.intelligence.planning.validator" not in modules, name
        assert "app.intelligence.planning.admission" not in modules, name
        assert "app.intelligence.planning.codes" not in modules, name
        assert "validate_plan" not in text and "PlanAdmission" not in text, name
        assert not [
            m for m in modules
            if m.startswith(("app.core", "app.models", "app.services", "app.api", "app.database", "app.repositories", "app.schemas", "app.main"))
        ], name


def test_the_import_scanner_flags_synthetic_violations():
    violations = [
        "from app.intelligence.gateway.gateway import Gateway",
        "from app.intelligence.gateway.contracts import ModelCallRequest",
        "from app.intelligence.planning.validator import validate_plan",
        "from app.intelligence.planning.admission import PlanAdmission",
        "from app.intelligence.planning.codes import RejectionCode",
        "from app.core.authorization import Permission",
        "from app.services.approval_service import ApprovalService",
        "from sqlalchemy.orm import Session",
        "import random", "import time", "import os", "import logging", "import subprocess",
        "import hashlib", "import socket", "import requests",
    ]

    for snippet in violations:
        assert import_problems(snippet) != [], snippet

    clean = (
        "import copy\nimport dataclasses\nimport json\nimport re\n"
        "from jsonschema import Draft202012Validator\n"
        "from app.intelligence.planning.canonical import digest\n"
        "from app.intelligence.proposal.contracts import Projection\n"
    )
    assert import_problems(clean) == []


def test_the_gateway_static_scanner_finds_nothing_and_no_environment_is_read():
    for name, text in sources().items():
        assert scan_source(text) == [], name
        assert "os.environ" not in text and "getenv" not in text, name
        assert not re.search(r"\bprint\(|\bopen\(|\beval\(|\bexec\(", text), name


def test_no_credential_vocabulary_appears_in_the_package():
    hits = []

    for path in python_files(PROPOSAL_DIR):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CREDENTIAL_WORDS.search(line.split("#", 1)[0]):
                hits.append(f"{path.name}:{number}")

    assert hits == []


def test_there_is_no_clock_randomness_io_or_persistence():
    for name, text in sources().items():
        roots = {m.split(".")[0] for m in imported_modules(text)}

        assert not roots & FORBIDDEN_ROOTS, (name, roots & FORBIDDEN_ROOTS)


def test_there_is_no_executor_dispatcher_or_approval_surface():
    offenders = []

    for path in python_files(PROPOSAL_DIR):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if EXECUTION_WORDS.search(node.name):
                    offenders.append(f"{path.name}:{node.name}")
            if isinstance(node, ast.AsyncFunctionDef):
                offenders.append(f"{path.name}:async {node.name}")

    assert offenders == []


def test_nothing_that_is_imported_is_modified_and_no_global_state_exists():
    offenders = []

    for path in python_files(PROPOSAL_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_names = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_names.update(a.asname or a.name for a in node.names)

        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    base = target.value
                    while isinstance(base, ast.Attribute):
                        base = base.value
                    if isinstance(base, ast.Name) and base.id in imported_names:
                        offenders.append(f"{path.name}: assigns {base.id}.{target.attr}")
            if isinstance(node, ast.Global):
                offenders.append(f"{path.name}: global")
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if called in {"setattr", "delattr", "__setattr__", "globals", "locals", "exec", "eval"}:
                    offenders.append(f"{path.name}: {called}()")

        for node in tree.body:  # module level: no mutable containers
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
                    offenders.append(f"{path.name}: mutable module state")

    assert offenders == []


def test_running_a_projection_changes_nothing_in_the_planning_package():
    from app.intelligence.planning import capabilities, schema

    from test_intelligence_projection_contracts import run

    names = {
        module.__name__: sorted(vars(module)) for module in (capabilities, schema)
    }
    functions = (schema.build_plan_schema, capabilities.CapabilityRegistry, capabilities.schema_is_closed)
    digest = capabilities.REGISTRY_V1.digest

    assert run().ok

    assert {m.__name__: sorted(vars(m)) for m in (capabilities, schema)} == names
    assert (schema.build_plan_schema, capabilities.CapabilityRegistry, capabilities.schema_is_closed) == functions
    assert capabilities.REGISTRY_V1.digest == digest == capabilities.PINNED_REGISTRY_DIGEST


def test_no_application_module_outside_the_package_imports_it():
    offenders = []

    for path in python_files(APP):
        relative = path.relative_to(BACKEND).as_posix()

        if relative.startswith("app/intelligence/proposal/"):
            continue

        if re.search(
            r"intelligence\.proposal|intelligence\s+import\s+proposal",
            path.read_text(encoding="utf-8"),
        ):
            offenders.append(relative)

    assert offenders == []


def test_no_route_permission_table_or_migration_was_added():
    from app.main import app

    for route in app.routes:
        label = f"{getattr(route, 'path', '')} {getattr(getattr(route, 'endpoint', None), '__module__', '')}".lower()
        assert "proposal" not in label and "projection" not in label, label

    for relative in (
        "app/core/authorization.py",
        "app/core/authentication.py",
        "app/core/approval_authorization.py",
        "app/core/work_authorization.py",
        "app/core/skill_authorization.py",
        "app/core/memory_authorization.py",
    ):
        text = (BACKEND / relative).read_text(encoding="utf-8").lower()
        assert "proposal" not in text and "projection" not in text, relative

    import app.models  # noqa: F401

    assert sorted(n for n in Base.metadata.tables if n.startswith("intelligence")) == [
        "intelligence_budgets",
        "intelligence_model_calls",
    ]

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == ["a7d3e91c4b26"]


def test_the_projection_documentation_exists_and_states_the_boundary():
    path = BACKEND / "docs" / "intelligence" / "PROJECTION_CONTRACT.md"
    text = path.read_text(encoding="utf-8")

    assert path.stat().st_size > 3000
    for required in (
        "proj.v1",
        "plan-instructions.v1",
        "ff48f1c23aecca5b67548c2845df0b7dbf2165af9c271b9212c1d8448fdf87bb",
        "projection_digest",
        "PROJECTION_STALE",
        "not an authorisation",
        "replay",
    ):
        assert required.lower() in text.lower(), required
