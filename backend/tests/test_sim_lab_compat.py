"""SIM-FREEZE-IMPL-01 -- compatibilidade ESTATICA do produto atual com o laboratorio SIM congelado.

Sem Docker, sem rede, sem escrever no laboratorio: so le `sim/stack/lab/lab_config.yaml`,
`sim/stack/*`, `backend/requirements.txt` e `backend/migrations`.

SCOPE_NOTE: esta verificacao NAO comprova compatibilidade operacional completa. Ela mostra que o
perfil congelado do laboratorio ainda e ESTRUTURALMENTE aceito pelo `Settings` atual, que a
inteligencia nao liga por acidente e que a cadeia Alembic estende o head congelado de forma
aditiva. Rodar o produto atual dentro do laboratorio exige reconstruir as imagens e re-provar o
laboratorio (alternativa B do SIM-FREEZE-RECONCILIATION-01), o que nao e feito aqui. O parse bem
sucedido do `Settings` sozinho nao e prova de compatibilidade operacional.
"""

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


SCOPE_NOTE = "NAO comprova compatibilidade operacional completa"

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
LAB_DIR = REPO_DIR / "sim" / "stack" / "lab"
LAB_CONFIG = LAB_DIR / "lab_config.yaml"
MIGRATIONS = BACKEND_DIR / "migrations" / "versions"

FROZEN_LAB_COMMIT = "d100ce23462989dd1047ceabf0a30dc7e5b0d62d"
FROZEN_LAB_HEAD = "5c1e7a90d2b4"
NON_SETTINGS_PROFILE_KEYS = {"TZ"}                      # variavel do sistema operacional, nao do Settings
ADDITIVE_MIGRATION_OPS = {"create_table", "create_index"}
LLM_TOKENS = ("openai", "anthropic", "ollama", "litellm", "langchain", "gemini", "claude_api", "llm_api")
PROD_DATABASE_URL = "postgresql+psycopg://sim:Zx9qLm4Tn7Vb2Kc8Rw5Yd1@sim-postgres:5432/auneron_sim"
PROD_API_KEY = "Q7d9R2m8L4x1P6v3N5k0T8z2B9c4W7y1"


# ---------------------------------------------------------------------------- leitura minima do YAML
def lab_text() -> str:
    return LAB_CONFIG.read_text(encoding="utf-8")


def parse_block(text: str, key: str) -> dict[str, str]:
    """Bloco `key:` de um nivel com pares `  CHAVE: valor` (sem depender de PyYAML)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.rstrip() == f"{key}:"), None)
    assert start is not None, f"bloco {key!r} ausente em lab_config.yaml"
    block: dict[str, str] = {}

    for line in lines[start + 1:]:
        if line.strip() and not line.startswith(" "):
            break

        match = re.match(r"^  ([A-Za-z0-9_]+):\s*(.*?)\s*$", line)

        if match:
            value = re.sub(r"\s+#.*$", "", match.group(2)).strip()
            block[match.group(1)] = value.strip('"').strip("'")

    return block


def settings_aliases() -> set[str]:
    return {
        field.validation_alias
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
    }


def production_settings(monkeypatch, profile: dict[str, str], **extra) -> Settings:
    values = {key: value for key, value in profile.items() if key not in NON_SETTINGS_PROFILE_KEYS}
    values.update(
        {
            "DATABASE_URL": PROD_DATABASE_URL,
            "API_KEY": PROD_API_KEY,
            "DEBUG": "false",
            "DATABASE_ECHO": "false",
        }
    )
    values.update(extra)
    # o validador de producao compara Settings.forwarded_allow_ips com o ambiente do processo
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", values["FORWARDED_ALLOW_IPS"])
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="module")
def profile() -> dict[str, str]:
    return parse_block(lab_text(), "backend_profile")


@pytest.fixture(scope="module")
def intervals() -> dict[str, str]:
    return parse_block(lab_text(), "intervals")


# ---------------------------------------------------------------------------- configuracao congelada
def test_the_frozen_lab_configuration_is_readable_and_unchanged_in_its_anchors():
    text = lab_text()
    target = parse_block(text, "target")

    assert re.search(r"^schema: sim\.lab\.v1$", text, re.M)
    assert target["commit"] == FROZEN_LAB_COMMIT
    assert target["alembic_head"] == FROZEN_LAB_HEAD


def test_the_note_about_the_limits_of_this_check_is_documented():
    assert SCOPE_NOTE in (__doc__ or "")
    assert "alternativa B" in (__doc__ or "")


# ---------------------------------------------------------------------------- estrutura x Settings
def test_every_profile_and_interval_key_is_a_known_settings_variable(profile, intervals):
    unknown = (set(profile) | set(intervals)) - settings_aliases() - NON_SETTINGS_PROFILE_KEYS

    assert unknown == set(), f"o laboratorio configura variaveis que o Settings nao conhece mais: {sorted(unknown)}"
    assert len(profile) >= 7 and len(intervals) >= 5          # a leitura nao foi vazia


def test_the_required_profile_values_are_exactly_the_frozen_ones(profile):
    assert profile["APP_ENV"] == "production"
    assert profile["EXPECTED_DATABASE_NAME"] == "auneron_sim"
    assert profile["EXPECTED_DATABASE_HOST"] == "sim-postgres"
    assert profile["MAINTENANCE_ENABLED"] == "true"           # SIM-MAINT-1
    assert profile["FORWARDED_ALLOW_IPS"] == "172.28.1.10/32,172.28.0.10/32"
    assert profile["BUSINESS_TIMEZONE"] == "America/Sao_Paulo"


def test_the_frozen_profile_builds_a_valid_production_settings(monkeypatch, profile, intervals):
    settings = production_settings(monkeypatch, {**profile, **intervals})

    assert settings.environment == "production"
    assert settings.maintenance_enabled is True
    assert settings.database_name == profile["EXPECTED_DATABASE_NAME"] == settings.expected_database_name
    assert settings.database_host == profile["EXPECTED_DATABASE_HOST"] == settings.expected_database_host
    assert settings.business_timezone == "America/Sao_Paulo"
    assert settings.work_skill_recovery_interval_seconds == int(intervals["WORK_SKILL_RECOVERY_INTERVAL_SECONDS"])
    assert settings.receivables_monitor_interval_seconds == int(intervals["RECEIVABLES_MONITOR_INTERVAL_SECONDS"])


def test_each_frozen_value_is_actually_enforced_not_merely_parsed(monkeypatch, profile, intervals):
    """Controle de sensibilidade: valores errados do perfil sao RECUSADOS (o aceite acima nao e trivial)."""
    merged = {**profile, **intervals}

    with pytest.raises(ValidationError):
        production_settings(monkeypatch, merged, EXPECTED_DATABASE_NAME="outro_banco")
    with pytest.raises(ValidationError):
        production_settings(monkeypatch, merged, MAINTENANCE_ENABLED="false")
    with pytest.raises(ValidationError):
        production_settings(monkeypatch, merged, WORK_SKILL_RECOVERY_INTERVAL_SECONDS="0")


# ---------------------------------------------------------------------------- inteligencia nao liga por acidente
def test_intelligence_is_off_for_the_frozen_lab_profile(monkeypatch, profile, intervals):
    settings = production_settings(monkeypatch, {**profile, **intervals})

    assert settings.intelligence_mode == "off"
    assert not [key for key in {**profile, **intervals} if key.startswith("INTELLIGENCE")]


@pytest.mark.parametrize("mode", ["shadow", "governed", "bounded"])
def test_production_refuses_any_intelligence_activation(monkeypatch, profile, intervals, mode):
    with pytest.raises(ValidationError):
        production_settings(monkeypatch, {**profile, **intervals}, INTELLIGENCE_MODE=mode)


def test_the_lab_stack_does_not_reference_intelligence_or_llm_providers():
    stack_files = [
        p for p in (REPO_DIR / "sim" / "stack").rglob("*")
        if p.is_file() and (p.suffix in {".yml", ".yaml", ".md"} or p.name.endswith("Dockerfile"))
    ]
    assert stack_files, "nenhum arquivo do stack foi lido"

    for path in stack_files:
        if "__pycache__" in path.parts:
            continue

        text = path.read_text(encoding="utf-8", errors="replace").lower()
        assert "intelligence_" not in text, path.name

        if path.suffix in {".yml", ".yaml"} or path.name.endswith("Dockerfile"):
            for token in LLM_TOKENS:
                assert token not in text, (path.name, token)


def test_backend_dependencies_contain_no_llm_provider_sdk():
    requirements = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8", errors="replace").lower()

    assert "fastapi" in requirements                         # o arquivo certo foi lido
    for token in LLM_TOKENS:
        assert token not in requirements, token


# ---------------------------------------------------------------------------- cadeia Alembic x head congelado
def read_revisions() -> dict[str, tuple[tuple[str, ...], Path]]:
    result: dict[str, tuple[tuple[str, ...], Path]] = {}

    for path in sorted(MIGRATIONS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        values: dict[str, object] = {}

        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                name, value = node.target.id, node.value          # `down_revision: Union[...] = ...`
            else:
                continue

            if name in {"revision", "down_revision"}:
                values[name] = ast.literal_eval(value)

        if "revision" not in values:
            continue

        down = values.get("down_revision")
        parents = (down,) if isinstance(down, str) else tuple(down or ())
        result[str(values["revision"])] = (parents, path)

    return result


def ancestors_after(revisions, head: str, stop: str) -> list[str]:
    """Revisoes entre `stop` (exclusive) e `head` (inclusive); falha se `stop` nao e ancestral de `head`."""
    chain: list[str] = []
    stack = [head]
    seen: set[str] = set()
    reached = False

    while stack:
        current = stack.pop()

        if current in seen:
            continue

        seen.add(current)

        if current == stop:
            reached = True
            continue

        chain.append(current)
        stack.extend(revisions[current][0])

    assert reached, f"{stop} nao e ancestral de {head}: a cadeia nao estende o head congelado do laboratorio"
    return chain


def upgrade_ops(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    ops = set()

    for node in ast.walk(upgrade):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ):
            ops.add(node.func.attr)

    return ops


def test_the_alembic_graph_has_a_single_head_that_extends_the_frozen_lab_head():
    revisions = read_revisions()
    parents = {p for ps, _ in revisions.values() for p in ps}
    heads = sorted(set(revisions) - parents)

    assert len(heads) == 1, heads
    assert FROZEN_LAB_HEAD in revisions
    after = ancestors_after(revisions, heads[0], FROZEN_LAB_HEAD)
    assert heads[0] in after or heads[0] == FROZEN_LAB_HEAD


def test_migrations_after_the_frozen_head_are_additive_only():
    """Migracoes novas que alterem/removam estruturas existentes tornam o head congelado do laboratorio
    incerto: exigem decisao explicita (alternativa B), nao passam por este teste em silencio."""
    revisions = read_revisions()
    parents = {p for ps, _ in revisions.values() for p in ps}
    head = sorted(set(revisions) - parents)[0]
    after = ancestors_after(revisions, head, FROZEN_LAB_HEAD)

    for revision in after:
        ops = upgrade_ops(revisions[revision][1])
        assert ops, revision
        assert ops <= ADDITIVE_MIGRATION_OPS, (revision, sorted(ops - ADDITIVE_MIGRATION_OPS))


def test_the_additive_detector_would_catch_a_destructive_migration(tmp_path):
    destructive = tmp_path / "destructive.py"
    destructive.write_text(
        'revision = "zz"\ndown_revision = "5c1e7a90d2b4"\n\n\ndef upgrade():\n    op.drop_column("t", "c")\n    op.create_table("n")\n',
        encoding="utf-8",
    )

    assert upgrade_ops(destructive) == {"drop_column", "create_table"}
    assert not upgrade_ops(destructive) <= ADDITIVE_MIGRATION_OPS


def test_the_ancestry_detector_rejects_unrelated_chains():
    unrelated = {"b": (("a",), Path("b.py")), "a": ((), Path("a.py"))}

    with pytest.raises(AssertionError, match="nao e ancestral"):
        ancestors_after(unrelated, "a", "b")          # "b" e filho de "a", nao ancestral

    assert ancestors_after(unrelated, "b", "a") == ["b"]
    assert read_revisions()[FROZEN_LAB_HEAD][0], "o head congelado do laboratorio deveria ter pai"
