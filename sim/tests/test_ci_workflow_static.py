"""
Gate ESTATICO do workflow `.github/workflows/sim-ci.yml` (SIM CI, 8o commit).

Le o YAML como TEXTO/estrutura e executa, em subprocessos Python locais e sobre fixtures sinteticas em `tmp_path`, os dois programas
que o workflow embute (o veredito e a camada de deteccao de rede). Deterministico: nenhum servico externo, nenhum Docker, nenhum
banco, nenhuma tarefa agendada, nenhum PowerShell. Os blocos PowerShell sao verificados so por analise textual aqui; a sintaxe
PowerShell e conferida fora dos testes (analisador do PowerShell) e o comportamento real so no GitHub Actions.

LIMITES ASSUMIDOS: a camada de rede e DETECCAO/PREVENCAO PARCIAL (so processos Python que carreguem o sitecustomize) e a deteccao de
processos remanescentes e best-effort. Estes testes provam esses limites; nao provam isolamento de rede nem ausencia de descendentes.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from sim.tests.test_oracle_v21 import ALLOWED

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sim-ci.yml"
EXACT_RULE = r"^\.github/workflows/sim-ci\.yml$"
PREVIOUS_SUITE_SIZE = 1427                      # coleta do pytest antes da criacao deste arquivo (com parametrizacao)

TEXT = WORKFLOW.read_text(encoding="utf-8")
DOC = yaml.safe_load(TEXT)
ON = DOC.get("on", DOC.get(True))               # PyYAML le a chave `on` como booleano True
JOB = DOC["jobs"]["sim-tests"]
STEPS = JOB["steps"]
RUNS = [step["run"] for step in STEPS if "run" in step]
ALL_RUN_TEXT = "\n".join(RUNS)


def step_named(prefix: str) -> dict:
    """Passo cujo `name` COMECA por `prefix` (os nomes tem acentos; os prefixos aqui sao ASCII)."""
    found = [s for s in STEPS if s.get("name", "").startswith(prefix)]
    assert len(found) == 1, prefix
    return found[0]


def here_string(variable: str) -> str:
    """Programa Python embutido num here-string PowerShell (`$variable = @' ... '@`), no texto JA analisado pelo YAML."""
    for run in RUNS:
        match = re.search(r"\$" + variable + r" = @'\n(.*?)\n'@", run, re.S)
        if match:
            return match.group(1) + "\n"
    raise AssertionError("here-string ausente: " + variable)


VERDICT = here_string("verdict")
TRAP = here_string("trap")


# ----------------------------------------------------------------------------------------------------------- estrutura
def test_the_workflow_is_one_job_with_the_designed_steps():
    assert list(DOC["jobs"]) == ["sim-tests"]
    assert DOC["name"] == "SIM CI"
    assert [step.get("uses", "run") for step in STEPS].count("run") == 6
    assert len(STEPS) == 9
    assert JOB["runs-on"] == "windows-latest"                # decisao do PO: Windows e a plataforma principal (Linux adiado)
    assert DOC["defaults"]["run"]["shell"] == "pwsh"


def test_permissions_are_read_only_and_there_are_no_secrets_or_dangerous_triggers():
    assert DOC["permissions"] == {"contents": "read"}
    assert "permissions" not in JOB                           # nada eleva as permissoes no nivel do job
    assert "pull_request_target" not in ON and "schedule" not in ON and "workflow_run" not in ON
    assert "secrets." not in TEXT and "secrets:" not in TEXT
    assert "GITHUB_TOKEN" not in TEXT and "id-token" not in TEXT


def test_triggers_cover_exactly_sim_and_the_workflow_itself():
    paths = ["sim/**", ".github/workflows/sim-ci.yml"]
    assert ON["push"]["branches"] == ["main", "review/**"] and ON["push"]["paths"] == paths
    assert ON["pull_request"]["branches"] == ["main"] and ON["pull_request"]["paths"] == paths
    assert "workflow_dispatch" in ON
    assert DOC["concurrency"]["cancel-in-progress"] is True and "github.ref" in DOC["concurrency"]["group"]


def test_checkout_is_full_history_without_persisted_credentials_and_python_is_311():
    checkout = step_named("Baixar")
    assert checkout["uses"].startswith("actions/checkout@") and checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    setup = step_named("Configurar Python")
    assert setup["uses"].startswith("actions/setup-python@") and setup["with"]["python-version"] == "3.11"
    assert setup["with"]["cache-dependency-path"] == "sim/requirements.txt"


def test_only_the_declared_sim_dependencies_are_installed():
    assert ALL_RUN_TEXT.count("pip install") == 1 and "pip install -r sim/requirements.txt" in ALL_RUN_TEXT
    assert "pip check" in ALL_RUN_TEXT
    assert not re.search(r"\b(npm|npx|choco|winget|apt|apt-get|brew)\b", ALL_RUN_TEXT)


def test_timeouts_leave_room_for_the_post_conditions():
    assert JOB["timeout-minutes"] <= 30
    assert step_named("Executar")["timeout-minutes"] < JOB["timeout-minutes"]


def test_post_condition_steps_always_run_and_the_pytest_exit_code_is_preserved():
    always = [s for s in STEPS if s.get("if") == "${{ always() }}"]
    assert [s["name"].split(" ")[0] for s in always] == ["Coletar", "Veredito", "Publicar"]
    pytest_run = step_named("Executar")["run"]
    assert "$code = $LASTEXITCODE" in pytest_run and pytest_run.rstrip().endswith("exit $code")
    assert "pytest.exit" in pytest_run and "$env:SIM_EVIDENCE_DIR\\sim-junit.xml" in pytest_run
    assert "-p no:cacheprovider" in pytest_run and "--tb=short" in pytest_run
    assert "--showlocals" not in TEXT and not re.search(r"\s-l\s", pytest_run)
    assert "--maxfail" not in pytest_run and "continue-on-error" not in TEXT


def test_directories_are_isolated_from_the_workspace_and_the_private_one_is_never_uploaded():
    guard = step_named("Pr")["run"]
    for name in ("sim-public", "sim-private", "auneron-sim-home", "sim-net-trap"):
        assert re.search(r"Join-Path \$env:RUNNER_TEMP '" + name + "'", guard), name
    assert "$d.StartsWith($env:GITHUB_WORKSPACE, [System.StringComparison]::OrdinalIgnoreCase)" in guard
    assert "throw \"directory inside the workspace" in guard
    upload = step_named("Publicar")
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["path"] == "${{ runner.temp }}\\sim-public"
    assert "sim-private" not in json.dumps(upload) and "auneron-sim-home" not in json.dumps(upload)
    assert upload["with"]["retention-days"] == 7 and upload["with"]["retention-days"] <= 14
    assert "runner." not in json.dumps(JOB["env"])               # o contexto `runner` nao existe no env do job


def test_no_real_docker_database_or_scheduled_task_operation_and_no_environment_dump():
    assert not re.search(r"docker|psql|pg_dump|pg_restore|alembic|sqlcmd|schtasks|\bat\.exe", ALL_RUN_TEXT, re.I)
    assert not re.search(r"(Register|Start|Stop|Unregister|New|Set|Enable|Disable)-ScheduledTask|Register-ScheduledJob|Invoke-Sqlcmd", ALL_RUN_TEXT)
    assert "Get-ScheduledTask" in ALL_RUN_TEXT                   # somente leitura da lista de tarefas
    assert not re.search(r"Get-ChildItem\s+Env:|\bgci\s+env:|\bdir\s+env:|printenv|GetEnvironmentVariables|Get-Variable|\bset\s*$", ALL_RUN_TEXT, re.I | re.M)
    assert "Write-Host $env" not in ALL_RUN_TEXT and "echo $env" not in ALL_RUN_TEXT.lower()


def test_run_blocks_are_ascii_only_and_embedded_programs_compile():
    for run in RUNS:
        assert all(ord(char) < 128 for char in run)
    for source in (VERDICT, TRAP):
        ast.parse(source)
    assert VERDICT.startswith("import json") and TRAP.startswith("import os")


def test_the_test_floor_is_an_integer_not_below_the_previous_suite():
    floor = JOB["env"]["SIM_MIN_TESTS"]
    assert re.fullmatch(r"[0-9]+", floor) and int(floor) >= PREVIOUS_SUITE_SIZE
    assert "coleta real do pytest" in TEXT                     # a origem do piso esta documentada no cabecalho


def test_the_header_documents_the_known_limitations_without_claiming_isolation():
    header = TEXT.split("name: SIM CI", 1)[0]
    assert "PARCIAL" in header and "NAO e isolamento de rede" in header
    assert "best-effort" in header and "Nao identifica todos os descendentes" in header
    assert "Destinos de rede brutos ficam SO no diretorio privado" in header
    assert "isolamento completo" not in TEXT.lower() and "bloqueio absoluto" not in TEXT.lower()
    assert "parcial" in step_named("Camada")["name"]


def test_process_detection_declares_both_best_effort_indicators():
    facts = step_named("Coletar fatos")["run"]
    assert "leftover_workspace_processes" in facts and "leftover_new_processes" in facts
    assert "CommandLine.Contains($env:GITHUB_WORKSPACE)" in facts and "ToFileTimeUtc()" in facts
    assert "procs_before.json" in step_named("Pr")["run"] and "procs_before.json" in facts
    assert VERDICT.count("best effort") == 2


# ------------------------------------------------------------------------------------------------ evidencia publica
def test_raw_network_destinations_stay_private_and_only_sanitized_counts_are_published():
    for run in RUNS:
        for line in run.splitlines():
            if "net_trap.log" in line:
                assert "SIM_PRIVATE_DIR" in line, line
    facts = step_named("Coletar fatos")["run"]
    assert "net_events_total" in facts and "net_events_by_kind" in facts
    assert "-notmatch '^[a-z_]+$'" in facts and "($line -split ' ', 2)[0]" in facts      # so o TIPO do evento sobe, nunca o destino
    assert not re.search(r"Copy-Item|Move-Item|Compress-Archive|Out-File[^\n]*net_trap", facts)
    assert "net_trap" not in VERDICT
    assert "SIM_NET_TRAP_LOG" in step_named("Executar")["run"]
    publish_names = {"sim-junit.xml", "sim-pytest.log", "pytest.exit", "precondition.ok", "facts.json", "ci-verdict.json"}
    assert "tasks_before.json" not in VERDICT and "tasks_before.json" not in json.dumps(step_named("Publicar"))
    assert all(name in TEXT for name in publish_names)


# ------------------------------------------------------------------------------------------------ fronteira (ALLOWED)
def accepted(path: str) -> bool:
    return any(re.match(rule, path) for rule in ALLOWED)


def test_allowed_accepts_exactly_the_workflow_path_under_github():
    assert accepted(".github/workflows/sim-ci.yml")
    rules = [rule for rule in ALLOWED if ".github" in rule]
    assert rules == [EXACT_RULE]
    assert accepted("sim/tests/test_ci_workflow_static.py")        # o teste estatico ja e coberto pela regra existente de sim/tests
    assert not any(rule != EXACT_RULE and re.match(rule, ".github/workflows/sim-ci.yml") for rule in ALLOWED)


@pytest.mark.parametrize("path", [
    ".github/workflows/backend-ci.yml", ".github/workflows/other.yml", ".github/workflows/sim-ci.yaml",
    ".github/workflows/sim-ci.yml.bak", ".github/workflows/sub/sim-ci.yml", ".github/workflows/Sim-CI.yml",
    ".github/workflows/sim-ci.yml/extra", ".github/CODEOWNERS", ".github/dependabot.yml", ".github/ISSUE_TEMPLATE/bug.md",
    "x/.github/workflows/sim-ci.yml", ".github/workflows/", ".github/workflows", ".github",
])
def test_every_other_github_path_stays_forbidden(path):
    assert not accepted(path)


def test_backend_and_frontend_stay_outside_the_allowed_set():
    assert not accepted("backend/app/main.py") and not accepted("frontend/src/main.tsx")


# ----------------------------------------------------------------------------------------------------------- veredito
FLOOR = 5
DEFAULT = object()
GOOD_FACTS = {"new_tasks": [], "dirty_files": 0, "leftover_workspace_processes": 0, "leftover_new_processes": 0,
              "net_events_total": 0, "net_events_by_kind": {}}


def suite(passed=0, failed=0, errored=0, skipped=0, declared=None):
    total = passed + failed + errored + skipped
    attrs = declared or {"tests": total, "failures": failed, "errors": errored, "skipped": skipped}
    cases = ['<testcase classname="t" name="ok%d"/>' % i for i in range(passed)]
    cases += ['<testcase classname="t" name="bad%d"><failure message="m">m</failure></testcase>' % i for i in range(failed)]
    cases += ['<testcase classname="t" name="err%d"><error message="m">m</error></testcase>' % i for i in range(errored)]
    cases += ['<testcase classname="t" name="skip%d"><skipped type="pytest.skip" message="m">m</skipped></testcase>' % i for i in range(skipped)]
    return '<testsuite name="pytest" errors="%s" failures="%s" skipped="%s" tests="%s">%s</testsuite>' % (
        attrs["errors"], attrs["failures"], attrs["skipped"], attrs["tests"], "".join(cases))


def wrap(*suites):
    return '<?xml version="1.0" encoding="utf-8"?><testsuites>' + "".join(suites) + "</testsuites>"


def make_evidence(tmp_path, *, junit=DEFAULT, exit_code="0", facts=DEFAULT, precondition=True):
    evidence = tmp_path / "public"
    evidence.mkdir()
    if precondition:
        (evidence / "precondition.ok").write_text("ok", encoding="ascii")
    junit = wrap(suite(passed=FLOOR)) if junit is DEFAULT else junit
    if junit is not None:
        (evidence / "sim-junit.xml").write_bytes(junit if isinstance(junit, bytes) else junit.encode("utf-8"))
    if exit_code is not None:
        (evidence / "pytest.exit").write_text(exit_code, encoding="ascii")
    facts = dict(GOOD_FACTS) if facts is DEFAULT else facts
    if facts is not None:
        (evidence / "facts.json").write_text(facts if isinstance(facts, str) else json.dumps(facts), encoding="utf-8")
    return evidence


def run_verdict(evidence, *, floor=FLOOR, summary=None):
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "SIM_EVIDENCE_DIR": str(evidence), "SIM_MIN_TESTS": str(floor)}
    if summary is not None:
        env["GITHUB_STEP_SUMMARY"] = str(summary)
    done = subprocess.run([sys.executable, "-I", "-c", VERDICT], env=env, capture_output=True, text=True, timeout=120)
    report = Path(evidence) / "ci-verdict.json"
    return done.returncode, (json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None), done.stdout


FUNCTIONAL, INFRASTRUCTURE, ISOLATION, EVIDENCE = 1, 2, 4, 8
CASES = {
    "all good": (dict(), 0),
    "pytest failures": (dict(junit=wrap(suite(passed=FLOOR, failed=2)), exit_code="1"), FUNCTIONAL),
    "pytest errors": (dict(junit=wrap(suite(passed=FLOOR, errored=1)), exit_code="1"), FUNCTIONAL),
    "unexpected skips": (dict(junit=wrap(suite(passed=FLOOR, skipped=1))), FUNCTIONAL),
    "suite shrank": (dict(junit=wrap(suite(passed=FLOOR - 1))), FUNCTIONAL),
    "no tests collected": (dict(junit=wrap(suite()), exit_code="5"), FUNCTIONAL),
    "exit 2 interrupted": (dict(exit_code="2"), INFRASTRUCTURE),
    "exit 3 internal error": (dict(exit_code="3"), INFRASTRUCTURE),
    "exit 4 usage error": (dict(exit_code="4"), INFRASTRUCTURE),
    "killed (large exit code)": (dict(exit_code="3221225786"), INFRASTRUCTURE),
    "pytest.exit missing": (dict(exit_code=None), INFRASTRUCTURE),
    "pytest.exit unreadable": (dict(exit_code="not-a-number"), EVIDENCE),
    "preconditions marker missing": (dict(precondition=False), INFRASTRUCTURE),
    "junit missing": (dict(junit=None), EVIDENCE),
    "junit empty": (dict(junit=""), EVIDENCE),
    "junit invalid xml": (dict(junit="<testsuites><testsuite"), EVIDENCE),
    "junit binary garbage": (dict(junit=b"\x00\xff\xfe\x01"), EVIDENCE),
    "junit without testsuite": (dict(junit='<?xml version="1.0"?><testsuites></testsuites>'), EVIDENCE),
    "junit attributes disagree": (dict(junit=wrap(suite(passed=FLOOR, declared={"tests": FLOOR + 3, "failures": 0, "errors": 0, "skipped": 0}))), EVIDENCE),
    "junit attribute not numeric": (dict(junit=wrap(suite(passed=FLOOR, declared={"tests": "x", "failures": 0, "errors": 0, "skipped": 0}))), EVIDENCE),
    "exit 0 contradicts failures": (dict(junit=wrap(suite(passed=FLOOR, failed=1)), exit_code="0"), FUNCTIONAL | EVIDENCE),
    "facts missing": (dict(facts=None), EVIDENCE),
    "facts invalid json": (dict(facts="{not json"), EVIDENCE),
    "facts indicator absent": (dict(facts={k: v for k, v in GOOD_FACTS.items() if k != "dirty_files"}), EVIDENCE),
    "facts collection error": (dict(facts={**{k: v for k, v in GOOD_FACTS.items() if k != "new_tasks"}, "new_tasks_error": "Boom"}), INFRASTRUCTURE),
    "new scheduled tasks": (dict(facts={**GOOD_FACTS, "new_tasks": ["\\RUN-1"]}), ISOLATION),
    "working tree changed": (dict(facts={**GOOD_FACTS, "dirty_files": 1}), ISOLATION),
    "leftover workspace process": (dict(facts={**GOOD_FACTS, "leftover_workspace_processes": 1}), ISOLATION),
    "leftover new process": (dict(facts={**GOOD_FACTS, "leftover_new_processes": 2}), ISOLATION),
    "external network attempts": (dict(facts={**GOOD_FACTS, "net_events_total": 3, "net_events_by_kind": {"connect": 3}}), ISOLATION),
    "failures plus isolation": (dict(junit=wrap(suite(passed=FLOOR, failed=1)), exit_code="1", facts={**GOOD_FACTS, "dirty_files": 2}), FUNCTIONAL | ISOLATION),
    "infrastructure plus evidence": (dict(exit_code="2", facts=None), INFRASTRUCTURE | EVIDENCE),
    "all four categories": (dict(junit=wrap(suite(passed=FLOOR, failed=1)), exit_code="0", facts={**GOOD_FACTS, "dirty_files": 1}, precondition=False),
                            FUNCTIONAL | INFRASTRUCTURE | ISOLATION | EVIDENCE),
    "several suites are summed": (dict(junit=wrap(suite(passed=3), suite(passed=2))), 0),
    "nested suites count once": (dict(junit='<testsuites><testsuite name="outer" tests="5" failures="0" errors="0" skipped="0">'
                                            + suite(passed=5) + "</testsuite></testsuites>"), 0),
    "root is a testsuite": (dict(junit=suite(passed=FLOOR)), 0),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_verdict_classifies_each_scenario_with_an_unambiguous_exit_code(tmp_path, name):
    kwargs, expected = CASES[name]
    code, report, stdout = run_verdict(make_evidence(tmp_path, **kwargs))
    assert code == expected, (stdout, report)
    assert report is not None and report["bitmask"] == expected
    assert ("OK" in stdout.splitlines()[0]) == (expected == 0)
    categories = {"FUNCTIONAL": FUNCTIONAL, "INFRASTRUCTURE": INFRASTRUCTURE, "ISOLATION": ISOLATION, "EVIDENCE": EVIDENCE}
    assert sum(categories[c] for c in report["categories"]) == expected
    assert all(report["categories"][c] for c in report["categories"])               # nunca categoria vazia


def test_aggregation_sums_leaf_suites_and_reports_the_totals(tmp_path):
    code, report, _ = run_verdict(make_evidence(tmp_path, junit=wrap(suite(passed=3), suite(passed=1, failed=1), suite(skipped=1)), exit_code="1"))
    assert report["totals"] == {"suites": 3, "tests": 6, "failures": 1, "errors": 0, "skipped": 1}
    assert code == FUNCTIONAL


def test_a_missing_evidence_directory_is_an_infrastructure_failure_and_writes_nothing(tmp_path):
    missing = tmp_path / "never-created"
    code, report, stdout = run_verdict(missing)
    assert code == INFRASTRUCTURE and report is None and not missing.exists()
    assert "[INFRASTRUCTURE]" in stdout


def test_the_verdict_appends_a_summary_with_categories_and_counts_only(tmp_path):
    facts = {**GOOD_FACTS, "net_events_total": 2, "net_events_by_kind": {"getaddrinfo": 2}}
    summary = tmp_path / "summary.md"
    code, report, stdout = run_verdict(make_evidence(tmp_path, facts=facts), summary=summary)
    text = summary.read_text(encoding="utf-8")
    assert code == ISOLATION and "[ISOLATION]" in text
    assert "network attempts recorded by the partial detection layer (counts only): 2" in text
    assert text.startswith("```\n") and text.rstrip().endswith("```")
    assert "example" not in text and "203.0.113" not in text                           # destinos nunca aparecem


def test_the_verdict_is_deterministic(tmp_path):
    results = []
    for name in ("a", "b"):
        base = tmp_path / name
        base.mkdir()
        results.append(run_verdict(make_evidence(base, junit=wrap(suite(passed=FLOOR, failed=1)), exit_code="1")))
    assert results[0][0] == results[1][0] == FUNCTIONAL and results[0][1] == results[1][1] and results[0][2] == results[1][2]


# --------------------------------------------------------------------------------------------- camada de deteccao de rede
def run_python(code, env_extra, *flags):
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), **env_extra}
    return subprocess.run([sys.executable, *flags, "-c", code], env=env, capture_output=True, text=True, timeout=120)


PROBE = '''
import json
import socket
import sys

out = {"sitecustomize": getattr(sys.modules.get("sitecustomize"), "__file__", "")}
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen(1)
client = socket.socket()
client.connect(("127.0.0.1", server.getsockname()[1]))
out["loopback"] = "ok"
client.close()
server.close()
attempts = {
    "getaddrinfo": lambda: socket.getaddrinfo("blocked-host.invalid", 80),
    "gethostbyname": lambda: socket.gethostbyname("blocked-host.invalid"),
    "gethostbyname_ex": lambda: socket.gethostbyname_ex("blocked-host.invalid"),
    "connect": lambda: socket.socket().connect(("203.0.113.1", 9)),
    "connect_ex": lambda: socket.socket().connect_ex(("203.0.113.1", 9)),
    "sendto": lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("203.0.113.1", 9)),
}
for label, attempt in attempts.items():
    try:
        attempt()
        out[label] = "NOT BLOCKED"
    except OSError as exc:
        out[label] = "blocked: " + str(exc)
print(json.dumps(out))
'''


def trap_dir(tmp_path):
    directory = tmp_path / "trap"
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(TRAP, encoding="utf-8")
    return directory


def test_the_detection_layer_allows_loopback_and_blocks_and_counts_external_attempts(tmp_path):
    directory = trap_dir(tmp_path)
    log = tmp_path / "private.log"
    done = run_python(PROBE, {"PYTHONPATH": str(directory), "SIM_NET_TRAP_LOG": str(log)})
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["sitecustomize"].startswith(str(directory)) and out["loopback"] == "ok"
    for label in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "connect", "connect_ex", "sendto"):
        assert out[label].startswith("blocked: external network blocked by the SIM CI detection layer"), (label, out[label])
        assert "invalid" not in out[label] and "203.0.113" not in out[label]            # a mensagem nunca carrega o destino
    kinds = [line.split(" ", 1)[0] for line in log.read_text(encoding="utf-8").splitlines()]
    assert sorted(set(kinds)) == ["connect", "connect_ex", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "sendto"]
    assert all(re.fullmatch(r"[a-z_]+", kind) for kind in kinds)                         # tipos sanitizaveis (so letras e sublinhado)


def test_the_detection_layer_logs_nothing_when_no_log_path_is_configured(tmp_path):
    directory = trap_dir(tmp_path)
    done = run_python(PROBE, {"PYTHONPATH": str(directory)})
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert done.returncode == 0 and out["getaddrinfo"].startswith("blocked:") and not list(tmp_path.glob("*.log"))


def test_known_limitation_isolated_python_does_not_load_the_detection_layer(tmp_path):
    directory = trap_dir(tmp_path)
    code = "import sys; print(getattr(sys.modules.get('sitecustomize'), '__file__', ''))"
    loaded = run_python(code, {"PYTHONPATH": str(directory)})
    isolated = run_python(code, {"PYTHONPATH": str(directory)}, "-I")
    replaced = run_python(code, {"PYTHONPATH": str(tmp_path)})                           # ambiente substituido: o diretorio da armadilha some
    assert loaded.stdout.strip().startswith(str(directory))
    assert not isolated.stdout.strip().startswith(str(directory))                        # -I ignora PYTHONPATH
    assert not replaced.stdout.strip().startswith(str(directory))


def test_known_limitation_a_subprocess_with_a_replaced_environment_is_not_covered_by_the_wrapper(tmp_path):
    """Documenta (sem acessar a rede) que o escape existe: sem a camada, a funcao de resolucao NAO esta envolvida pelo wrapper da
    armadilha. Nenhuma resolucao e feita."""
    directory = trap_dir(tmp_path)
    code = "import socket; print(socket.getaddrinfo.__module__ if hasattr(socket.getaddrinfo, '__module__') else '', socket.getaddrinfo.__name__)"
    wrapped = run_python(code, {"PYTHONPATH": str(directory)})
    bare = run_python(code, {"PYTHONPATH": str(tmp_path)})
    assert wrapped.stdout.split()[-1] == "wrapper" and bare.stdout.split()[-1] == "getaddrinfo"
