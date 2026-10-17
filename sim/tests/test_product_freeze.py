"""
SIM-FREEZE-IMPL-01 -- contrato de congelamento: proveniencia historica,
escopo do SIM no historico e preservacao dos gates vivos.

Duas familias de testes:
  * repositorio REAL (somente leitura, objetos do Git): fatos imutaveis;
  * repositorios SINTETICOS em tmp_path (N01..N13): cada adulteracao antes
    proibida continua sendo detectada, ha um controle limpo/positivo e, quando
    um detector ingenuo falharia, o teste mostra o que ele deixaria passar.

Nada aqui escreve no repositorio real; o modulo prova isso (fixture final).
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from sim.runner.preflight import judge_preflight
from sim.tests import freeze_support as fs
from sim.tests.conftest import SIM_ROOT
from sim.tests.runner_support import good_preflight_facts
from sim.tests.runner_support import make_rim

REPO = SIM_ROOT.parent
LAB_CONFIG = SIM_ROOT / "stack" / "lab" / "lab_config.yaml"
BASELINE = fs.HISTORICAL_COMMITS["sim14_baseline"]
ALLOWED_SANDBOX = [r"^sim/tests/[^/]+$", r"^sim/driver/[^/]+$", r"^sim/collector/[^/]+$",
                   r"^\.github/workflows/sim-ci\.yml$"]
GIB = 2 ** 30


def real_state() -> tuple:
    return (
        fs.git(REPO, "rev-parse", "HEAD").stdout.strip(),
        fs.git(REPO, "for-each-ref", "--format=%(refname) %(objectname)").stdout,
        fs.git(REPO, "diff", "--cached", "--name-only").stdout,
    )


@pytest.fixture(scope="module", autouse=True)
def real_repository_is_never_modified():
    before = real_state()
    yield
    assert real_state() == before, "o teste de congelamento alterou o repositorio real"


# ----------------------------------------------------------------------------- sandbox sintetico
class Sandbox:
    """Repositorio Git descartavel; nunca toca o repositorio real."""

    FLAGS = ["-c", "user.name=freeze", "-c", "user.email=freeze@example.invalid", "-c", "commit.gpgsign=false",
             "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", "-c", "protocol.file.allow=always"]

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.run("init", "-q")

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", *self.FLAGS, "-C", str(self.path), *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")

        if check and result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} falhou: {result.stderr[-300:]}")

        return result

    def write(self, rel: str, text: str = "x\n") -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    def remove(self, rel: str) -> None:
        (self.path / rel).unlink()

    def commit(self, message: str) -> str:
        self.run("add", "-A")
        self.run("commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.run("rev-parse", "HEAD").stdout.strip()

    def tree(self, path: str, commit: str = "HEAD") -> str:
        return self.run("rev-parse", f"{commit}:{path}").stdout.strip()

    def seed(self) -> str:
        self.write("backend/app.py", "print('app')\n")
        self.write("frontend/index.html", "<html></html>\n")
        self.write("sim/driver/d.py", "d = 1\n")
        self.write("sim/collector/c.py", "c = 1\n")
        self.write("sim/s.py", "s = 1\n")
        self.write("docs/readme.md", "docs\n")
        return self.commit("base")


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(tmp_path / "repo")


def kinds(violations) -> list[str]:
    return [v.kind for v in violations]


# ----------------------------------------------------------------------------- REPOSITORIO REAL
@pytest.mark.parametrize("name", list(fs.HISTORICAL_COMMITS))
def test_historical_commit_is_present_and_an_ancestor_of_head(name):
    sha = fs.HISTORICAL_COMMITS[name]

    if not fs.require_commit(REPO, sha):
        pytest.skip(f"commit congelado {name} ausente (clone raso)")

    assert fs.git(REPO, "merge-base", "--is-ancestor", sha, "HEAD").returncode == 0


def test_frozen_product_trees_are_the_pinned_literals():
    for sha in fs.HISTORICAL_COMMITS.values():
        if not fs.require_commit(REPO, sha):
            pytest.skip("historia congelada ausente (clone raso)")

    assert fs.provenance_violations(REPO) == []
    assert re.fullmatch(r"[0-9a-f]{40}", fs.PINNED_PRODUCT_TREES["backend"])
    assert re.fullmatch(r"[0-9a-f]{40}", fs.PINNED_PRODUCT_TREES["frontend"])


def test_lab_config_matches_the_frozen_literals():
    config = yaml.safe_load(LAB_CONFIG.read_text(encoding="utf-8"))

    assert fs.lab_config_violations(config) == []
    assert config["target"]["commit"][:12] == "d100ce234629"          # tag das imagens provadas


def test_the_whole_real_history_obeys_the_scope_rule_for_every_frozen_base():
    for name, sha in fs.HISTORICAL_COMMITS.items():
        if not fs.require_commit(REPO, sha):
            pytest.skip("historia congelada ausente (clone raso)")

        assert [str(v) for v in fs.mixed_commit_violations(REPO, sha)] == [], name


def test_the_real_sim_is_additive_over_the_baseline_in_committed_history():
    from sim.tests.test_oracle_v21 import ALLOWED
    from sim.tests.test_oracle_v21 import HISTORICAL

    if not fs.require_commit(REPO, BASELINE):
        pytest.skip("baseline ausente (clone raso)")

    assert fs.additivity_violations(REPO, BASELINE, ALLOWED, head="HEAD", historical_prefixes=HISTORICAL) == []


def test_the_ci_workflow_still_pins_the_history_and_refuses_shallow_clones():
    workflow = (REPO / ".github" / "workflows" / "sim-ci.yml").read_text(encoding="utf-8")

    for sha in fs.HISTORICAL_COMMITS.values():
        assert sha in workflow, sha
    assert "git rev-parse --is-shallow-repository" in workflow and "shallow checkout" in workflow
    assert "fetch-depth: 0" in workflow
    assert "working tree is not clean before the tests" in workflow


# ----------------------------------------------------------------------------- GATES VIVOS PRESERVADOS
def test_live_gates_keep_the_strict_product_identity_contract():
    """A igualdade produto x imagens NAO foi relaxada: so mudou de lugar. Estes textos sao a aplicacao dela."""
    gates = (SIM_ROOT / "live" / "gates.py").read_text(encoding="utf-8")
    preflight = (SIM_ROOT / "runner" / "preflight.py").read_text(encoding="utf-8")
    nh = (SIM_ROOT / "runner" / "nh_phases.py").read_text(encoding="utf-8")

    assert 'git(lab, "diff", "--name-only", lab.config.commit, "HEAD", "--", "backend", "frontend")' in gates
    assert "and not product_diff and not state[\"bytes_mismatch\"]" in gates
    assert "and not state[\"tracked_diff\"]" in gates
    assert 'if p["head"] != d["repo_commit"] or not p["tree_clean"] or not p["index_empty"]' in preflight
    assert 'if p["backend_tree"] != d["trees"]["backend"] or p["frontend_tree"] != d["trees"]["frontend"]:' in preflight
    assert 'p["build_git_sha"] != d["repo_commit"] or p["build_dirty"] is not False' in preflight
    assert '"P2": {"backend_tree": git("rev-parse", "HEAD:backend"), "frontend_tree": git("rev-parse", "HEAD:frontend")}' in nh


def test_the_product_tests_were_not_removed_only_rescoped():
    """Nenhum dos quatro invariantes originais sumiu: cada teste ainda existe e chama o contrato novo."""
    expectations = {
        "test_driver.py": "test_product_under_test_is_unchanged_since_the_proven_images",
        "test_oracle_v21.py": "test_candidate_file_boundary_is_exactly_the_authorized_set",
        "test_runner_bundle.py": "test_the_1_5f_candidate_changes_no_frozen_component_nor_the_evaluator_nor_the_oracle",
        "test_runner_units.py": "test_the_1_5e_candidate_leaves_driver_collector_stack_live_generator_v21_oracle_backend_and_frontend_untouched",
    }

    for filename, name in expectations.items():
        source = (SIM_ROOT / "tests" / filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        body = ast.get_source_segment(source, function)
        assert "freeze_support" in body or "fs." in body, name


# ----------------------------------------------------------------------------- N01..N13 (sinteticos)
def test_n01_a_sim_commit_that_modifies_the_backend_is_detected(sandbox):
    base = sandbox.seed()
    sandbox.write("sim/s.py", "s = 2\n")
    clean = sandbox.commit("sim only")
    assert fs.mixed_commit_violations(sandbox.path, base) == []              # controle limpo

    sandbox.write("sim/s.py", "s = 3\n")
    sandbox.write("backend/app.py", "print('tampered')\n")
    bad = sandbox.commit("sim + backend tamper")

    found = fs.mixed_commit_violations(sandbox.path, base)
    assert [(v.kind, v.commit, v.paths) for v in found] == [("sim_commit_touches_product", bad, ("backend/app.py",))]
    assert clean != bad


def test_n02_a_sim_commit_that_adds_backend_or_frontend_files_is_detected(sandbox):
    base = sandbox.seed()
    sandbox.write("sim/new.py")
    sandbox.write("backend/new.py")
    sandbox.write("frontend/new.js")
    bad = sandbox.commit("sim + new product files")

    found = fs.mixed_commit_violations(sandbox.path, base)
    assert [v.commit for v in found] == [bad]
    assert set(found[0].paths) == {"backend/new.py", "frontend/new.js"}


def test_n03_a_mixed_commit_in_the_middle_of_the_range_is_found_not_just_the_last_diff(sandbox):
    base = sandbox.seed()
    sandbox.write("sim/a.py")
    sandbox.commit("sim 1")
    sandbox.write("sim/b.py")
    sandbox.remove("backend/app.py")
    middle = sandbox.commit("sim 2 + backend deletion")
    sandbox.write("sim/c.py")
    sandbox.commit("sim 3")
    sandbox.write("sim/d.py")
    sandbox.commit("sim 4")

    assert [v.commit for v in fs.mixed_commit_violations(sandbox.path, base)] == [middle]
    # detector ingenuo: olha so o ultimo commit -> nao ve nada
    assert fs.mixed_commit_violations(sandbox.path, "HEAD~1") == []


def test_n04_independent_backend_evolution_is_not_a_violation_positive_control(sandbox):
    base = sandbox.seed()
    sandbox.write("sim/a.py")
    sandbox.commit("sim 1")
    sandbox.write("backend/app.py", "print('feature')\n")
    sandbox.write("backend/new.py")
    sandbox.commit("backend only")
    sandbox.write("sim/b.py")
    sandbox.commit("sim 2")
    sandbox.write("frontend/index.html", "<html>v2</html>\n")
    sandbox.commit("frontend only")

    assert fs.mixed_commit_violations(sandbox.path, base) == []
    # o detector ABSOLUTO antigo (HEAD == base para o produto) acusaria esta historia legitima
    assert fs.product_divergence(sandbox.path, base) == ["backend/app.py", "backend/new.py", "frontend/index.html"]


def test_n05_changes_to_frozen_sim_components_are_detected_committed_staged_or_deleted(sandbox):
    base = sandbox.seed()
    frozen = ["sim/driver", "sim/collector"]
    assert fs.frozen_component_diff(sandbox.path, base, frozen) == []         # controle limpo

    sandbox.write("sim/driver/d.py", "d = 2\n")
    assert fs.frozen_component_diff(sandbox.path, base, frozen) == ["M\tsim/driver/d.py"]   # arvore de trabalho
    sandbox.run("add", "-A")
    (sandbox.path / "sim/driver/d.py").write_text("d = 1\n", encoding="utf-8", newline="\n")   # arvore de trabalho volta
    assert fs.frozen_component_diff(sandbox.path, base, frozen, cached=False) == []
    assert fs.frozen_component_diff(sandbox.path, base, frozen) == ["M\tsim/driver/d.py"]    # so o INDICE revela
    sandbox.write("sim/driver/d.py", "d = 2\n")
    sandbox.commit("tamper driver")
    sandbox.remove("sim/collector/c.py")
    sandbox.commit("remove collector")

    assert sorted(fs.frozen_component_diff(sandbox.path, base, frozen)) == ["D\tsim/collector/c.py", "M\tsim/driver/d.py"]
    assert fs.frozen_component_diff(sandbox.path, base, ["sim/s.py"]) == []   # fora do escopo congelado


def test_n06_the_sim_must_stay_additive_over_the_baseline(sandbox):
    base = sandbox.seed()
    sandbox.write("sim/tests/new_test.py")
    sandbox.commit("additive")
    assert fs.additivity_violations(sandbox.path, base, ALLOWED_SANDBOX, head="HEAD") == []   # controle

    sandbox.write("sim/s.py", "s = 99\n")
    sandbox.commit("modify baseline file")
    assert fs.additivity_violations(sandbox.path, base, ALLOWED_SANDBOX, head="HEAD") == ["nao aditivo: M\tsim/s.py"]

    sandbox.remove("sim/s.py")
    sandbox.commit("delete baseline file")
    assert fs.additivity_violations(sandbox.path, base, ALLOWED_SANDBOX, head="HEAD") == ["nao aditivo: D\tsim/s.py"]


def test_n07_stray_sim_paths_outside_the_authorised_rules_are_detected(sandbox):
    base = sandbox.seed()
    sandbox.write("sim/tests/ok.py")
    sandbox.write("sim/rogue/escape.py")
    assert fs.additivity_violations(sandbox.path, base, ALLOWED_SANDBOX) == [      # candidato ainda nao commitado
        "fora das regras autorizadas: sim/rogue/escape.py"]

    sandbox.commit("with stray")
    assert fs.additivity_violations(sandbox.path, base, ALLOWED_SANDBOX, head="HEAD") == [
        "fora das regras autorizadas: sim/rogue/escape.py"]
    assert fs.additivity_violations(sandbox.path, base, ALLOWED_SANDBOX + [r"^sim/rogue/"], head="HEAD") == []


def test_n08_historical_provenance_literals_cannot_be_altered_or_recomputed(sandbox):
    base = sandbox.seed()
    commits = {"frozen": base}
    pins = {"backend": sandbox.tree("backend", base), "frontend": sandbox.tree("frontend", base)}
    assert fs.provenance_violations(sandbox.path, commits, pins) == []         # controle

    mutated = dict(pins, backend=("0" if pins["backend"][0] != "0" else "1") + pins["backend"][1:])
    assert len(fs.provenance_violations(sandbox.path, commits, mutated)) == 1  # literal adulterado

    # o commit congelado e enderecado por conteudo: evoluir o backend depois NAO muda a prova ...
    sandbox.write("backend/app.py", "print('evolved')\n")
    sandbox.commit("backend evolves")
    assert fs.provenance_violations(sandbox.path, commits, pins) == []
    # ... e "recalcular a baseline a partir do checkout atual" e exatamente o que nao se aceita:
    recomputed = {"backend": sandbox.tree("backend"), "frontend": sandbox.tree("frontend")}
    assert fs.provenance_violations(sandbox.path, commits, recomputed) != []
    # as constantes reais nao batem num repositorio qualquer
    assert len(fs.provenance_violations(sandbox.path, {"frozen": base})) == 2


def test_n09_an_inconsistent_lab_configuration_is_detected():
    real = yaml.safe_load(LAB_CONFIG.read_text(encoding="utf-8"))
    assert fs.lab_config_violations(real) == []                                # controle

    for mutate, expected in (
        (lambda c: c["target"].__setitem__("commit", "0" * 40), "target.commit"),
        (lambda c: c["target"].__setitem__("alembic_head", "a7d3e91c4b26"), "target.alembic_head"),
        (lambda c: c["docker"]["images"].__setitem__("backend", "outro"), "docker.images"),
        (lambda c: c["docker"].pop("images"), "docker.images"),
        (lambda c: c.pop("target"), "target.commit"),
    ):
        config = yaml.safe_load(LAB_CONFIG.read_text(encoding="utf-8"))
        mutate(config)
        problems = fs.lab_config_violations(config)
        assert problems and any(expected in p for p in problems), (expected, problems)


def test_n10_merges_hide_nothing(sandbox):
    base = sandbox.seed()

    # (a) commit misto escondido num ramo lateral: a visao first-parent do merge nao o enxerga
    sandbox.run("checkout", "-q", "-b", "side")
    sandbox.write("sim/side.py")
    sandbox.write("backend/app.py", "print('smuggled')\n")
    hidden = sandbox.commit("side: sim + backend")
    sandbox.run("checkout", "-q", "main")
    sandbox.write("sim/main.py")
    sandbox.commit("main sim")
    sandbox.run("merge", "-q", "--no-ff", "-m", "merge side", "side")
    first_parent = sandbox.run("rev-list", "--first-parent", f"{base}..HEAD").stdout.split()
    assert hidden not in first_parent                                            # detector ingenuo (first-parent) perde
    assert [v.commit for v in fs.mixed_commit_violations(sandbox.path, base)] == [hidden]

    # (c) controle positivo: merge limpo de ramo so de produto com ramo so de SIM
    clean = Sandbox(sandbox.path.parent / "clean")
    cbase = clean.seed()
    clean.run("checkout", "-q", "-b", "prod")
    clean.write("backend/app.py", "print('prod')\n")
    clean.commit("prod only")
    clean.run("checkout", "-q", "main")
    clean.write("sim/x.py")
    clean.commit("sim only")
    clean.run("merge", "-q", "--no-ff", "-m", "merge prod", "prod")
    assert fs.mixed_commit_violations(clean.path, cbase) == []
    assert fs.merge_evil_paths(clean.path, clean.head()) == []


def test_n10b_an_evil_merge_that_hand_edits_the_product_is_detected(sandbox):
    base = sandbox.seed()
    sandbox.run("checkout", "-q", "-b", "side")
    sandbox.write("sim/side.py")
    sandbox.commit("side sim")
    sandbox.run("checkout", "-q", "main")
    sandbox.write("sim/main.py")
    sandbox.commit("main sim")
    sandbox.run("merge", "--no-ff", "--no-commit", "-q", "side")
    sandbox.write("backend/evil.py", "evil\n")                                    # conteudo proprio do merge
    sandbox.run("add", "-A")
    sandbox.run("commit", "-q", "-m", "evil merge")
    merge = sandbox.head()

    found = fs.mixed_commit_violations(sandbox.path, base)
    assert [(v.kind, v.commit, v.paths) for v in found] == [("evil_merge_touches_outside_sim", merge, ("backend/evil.py",))]
    # nenhum commit comum e misto: so a inspecao do merge revela
    assert [c for c in sandbox.run("rev-list", "--no-merges", f"{base}..HEAD").stdout.split()
            if fs.commit_paths(sandbox.path, c) and any(p.startswith("backend/") for p in fs.commit_paths(sandbox.path, c))] == []


def test_n10c_an_octopus_merge_is_examined_against_every_parent(sandbox):
    base = sandbox.seed()
    for name in ("one", "two"):
        sandbox.run("checkout", "-q", "-b", name, base)
        sandbox.write(f"sim/{name}.py")
        sandbox.commit(f"{name} sim")
    sandbox.run("checkout", "-q", "main")
    sandbox.run("merge", "--no-ff", "--no-commit", "-q", "one", "two")
    sandbox.write("frontend/evil.js", "evil\n")
    sandbox.run("add", "-A")
    sandbox.run("commit", "-q", "-m", "evil octopus")
    merge = sandbox.head()

    assert len(sandbox.run("rev-list", "--parents", "-n", "1", merge).stdout.split()) == 4   # 3 pais
    assert [(v.kind, v.paths) for v in fs.mixed_commit_violations(sandbox.path, base)] == [
        ("evil_merge_touches_outside_sim", ("frontend/evil.js",))]


def test_n11_a_rename_that_moves_product_code_into_the_sim_is_not_hidden(sandbox):
    base = sandbox.seed()
    sandbox.run("mv", "backend/app.py", "sim/tests_app.py")
    moved = sandbox.commit("move product file into sim")

    found = fs.mixed_commit_violations(sandbox.path, base)
    assert [(v.commit, v.paths) for v in found] == [(moved, ("backend/app.py",))]
    # detector com deteccao de renomeacao: --name-only mostra so o DESTINO e o produto some da lista
    naive = sandbox.run("diff-tree", "-r", "-M", "--no-commit-id", "--name-only", moved).stdout.split()
    assert naive == ["sim/tests_app.py"] and not any(p.startswith("backend/") for p in naive)


def test_n12_live_gates_refuse_a_product_or_image_identity_mismatch(sandbox):
    rim = make_rim()
    good = good_preflight_facts(rim)
    assert judge_preflight(good, rim, projected_growth_bytes=GIB) == []         # controle: gate vivo passa limpo

    cases = {
        "P1": lambda f: f["P1"].__setitem__("head", "0" * 40),
        "P2": lambda f: f["P2"].__setitem__("backend_tree", "0" * 40),
        "P2f": lambda f: f["P2"].__setitem__("frontend_tree", "0" * 40),
        "P1c": lambda f: f["P1"].__setitem__("tree_clean", False),
        "P5": lambda f: f["P5"].__setitem__("build_git_sha", "0" * 40),
        "P5d": lambda f: f["P5"].__setitem__("build_dirty", True),
    }
    for label, mutate in cases.items():
        facts = good_preflight_facts(rim)
        mutate(facts)
        failed = {f["check"] for f in judge_preflight(facts, rim, projected_growth_bytes=GIB)}
        assert failed == {label[:2]}, (label, failed)

    # a mesma consulta do LIVE-PREP (diff de produto contra o commit das imagens), em repositorio sintetico
    image_commit = sandbox.seed()
    assert fs.product_divergence(sandbox.path, image_commit) == []
    sandbox.write("backend/app.py", "print('newer product')\n")
    sandbox.commit("product moves on")
    assert fs.product_divergence(sandbox.path, image_commit) == ["backend/app.py"]


def test_n13_missing_history_never_skips_silently_in_ci(sandbox, tmp_path, monkeypatch):
    first = sandbox.seed()
    sandbox.write("sim/more.py")
    sandbox.commit("second")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", sandbox.path.as_uri(), str(shallow)], check=True,
                   capture_output=True)

    assert fs.commit_available(sandbox.path, first) and not fs.commit_available(shallow, first)
    assert fs.require_commit(shallow, first, ci=False) is False                  # local: o chamador pula
    with pytest.raises(fs.FreezeUnavailable):
        fs.require_commit(shallow, first, ci=True)                               # CI: falha, nao pula

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(fs.FreezeUnavailable):
        fs.require_commit(shallow, first)                                        # detecta o CI pelo ambiente
    monkeypatch.delenv("GITHUB_ACTIONS")
    assert fs.require_commit(shallow, first) is False


def test_negative_matrix_is_complete():
    names = {name for name in globals() if re.fullmatch(r"test_n(\d\d)[a-z]?_.+", name)}
    numbers = {int(re.match(r"test_n(\d\d)", n).group(1)) for n in names}

    assert numbers == set(range(1, 14))
