"""
Fases do harness live (PRE-LIVE do SIM-1.5, agora versionadas): preparo, stack com o override, provisionamento,
compose config real, build das imagens, DR-1..DR-8, LIVE-C0 e LIVE-C0b (floor), teardown e reconciliacao.

Cada fase grava a evidencia em `<lab_home>/<instancia>/evidence.json` (fora do repositorio). Falha real,
contrato incompativel, THRESHOLD_REVIEW_REQUIRED, isolamento inadequado ou necessidade de alterar o candidato
=> a fase REGISTRA e o operador PARA; nenhuma fase corrige o ambiente sozinha.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from sim.live import env as E
from sim.live import flow
from sim.live import judges
from sim.live import scenarios as S
from sim.live.fingerprint import delta
from sim.live.fingerprint import fingerprint
from sim.stack.lab import driver_lab
from sim.stack.lab import gates
from sim.stack.lab import provision
from sim.stack.lab import sqlro
from sim.stack.lab.config import REPO_ROOT
from sim.stack.lab.config import lab_home
from sim.stack.lab.guard import GuardViolation

CONTAINER_DIR = Path(__file__).resolve().parent / "container"
HISTORICAL = (".claude/", "Claude outputs/", "backend/DW3_patch_review_v2.patch",
              "backend/scripts/reset_test_password.py",
              "backend/tests/test_value33a_observed_fact_correlation_evidence.py",
              "backend/tests/test_value33b_concurrent_worker_race_evidence.py")
# padroes de morte do processo do DR-8 (regex sobre "<METODO> <url>")
CRASH_AFTER_CREATE = r"POST .*/accounts/$"
CRASH_AFTER_DECISION = r"POST .*/approvals/\d+/decision$"


def script(name: str) -> str:
    return (CONTAINER_DIR / name).read_text(encoding="utf-8")


def git(lab, *args) -> str:
    return lab.runner.run(["git", "-C", str(REPO_ROOT), *args]).out


def sql(lab, query: str) -> list:
    return sqlro.query(lab.runner, lab.config, query)


def http_items(plan: dict) -> list:
    """Itens que o COLLECTOR coleta. O item `cli` (proveniencia) e executado pelo harness e nao entra na contagem."""
    return [i for i in plan["items"] if i.get("channel", "http") == "http"]


def version_id(lab) -> int:
    value = lab.evidence.get("mark_paid_version_id")
    if not isinstance(value, int):
        raise GuardViolation("mark_paid_version_id ausente: o provisionamento (fase `provision`) captura o id (fail closed)")
    return value


# ----------------------------------------------------------------------------------- candidato (indice)
def candidate_manifest(lab) -> list:
    """[(sha256, caminho)] dos arquivos STAGED, lido do INDICE (nunca da arvore de trabalho)."""
    rows = []
    for name in sorted(git(lab, "diff", "--cached", "--name-only").split()):
        blob = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f":{name}"], capture_output=True).stdout
        rows.append((hashlib.sha256(blob).hexdigest(), name))
    return rows


def manifest_text(rows: list) -> str:
    return "".join(f"{digest} *{name}\n" for digest, name in rows)


def manifest_digest(rows: list) -> str:
    return hashlib.sha256(manifest_text(rows).encode("utf-8")).hexdigest()[:16]


def candidate_state(lab, approved: list | None = None) -> dict:
    """Estado do candidato: HEAD, indice, bytes do indice E da arvore de trabalho contra o manifesto aprovado."""
    rows = candidate_manifest(lab)
    approved = approved if approved is not None else rows
    approved_map = {name: digest for digest, name in approved}
    mismatch = []
    for digest, name in rows:
        work = (REPO_ROOT / name).read_bytes()
        if approved_map.get(name) != digest or hashlib.sha256(work).hexdigest() != digest:
            mismatch.append(name)
    mismatch += sorted(set(approved_map) - {n for _, n in rows})
    untracked = sorted(l[3:].strip().strip('"') for l in git(lab, "status", "--porcelain", "--untracked-files=all").splitlines()
                       if l.startswith("??"))
    frozen = json.loads((REPO_ROOT / "sim/tests/sim14_frozen_hashes.json").read_text(encoding="utf-8"))
    frozen = frozen.get("files", frozen)
    return {
        "head": git(lab, "rev-parse", "HEAD").strip(), "staged": [n for _, n in rows], "digest": manifest_digest(rows),
        "tracked_diff": git(lab, "diff", "--name-only").split(),
        "backend_frontend_staged": [n for _, n in rows if n.startswith(("backend/", "frontend/"))],
        "bytes_mismatch": mismatch, "untracked": untracked,
        "extra_untracked": judges.classify_untracked(untracked, list(HISTORICAL)),
        "sim14_hash_mismatches": [k for k, v in frozen.items()
                                  if hashlib.sha256((REPO_ROOT / k).read_bytes()).hexdigest() != v],
    }


# ----------------------------------------------------------------------------------- fases de preparo
def phase_prep(lab, approved: list | None = None) -> dict:
    """Guarda, snapshots DEV (antes), reuso mecanico do S-1 e preflight D-1.5.1-2 (produto inalterado)."""
    guard = gates.phase_guard(lab)
    before = gates.phase_snapshot(lab, "before")
    pre = gates.phase_snapshot(lab, "pre_canonical")
    source, s1 = E.find_s1_evidence()
    lab.evidence["gates"]["S-1"] = s1
    lab.evidence["s1_imported_from"] = source
    lab.save()
    reuse = gates.phase_s1_reuse(lab)
    product_diff = git(lab, "diff", "--name-only", lab.config.commit, "HEAD", "--", "backend", "frontend").split()
    rows = approved if approved is not None else candidate_manifest(lab)
    lab.evidence["approved_manifest"] = [list(r) for r in rows]
    lab.evidence["expected_head"] = git(lab, "rev-parse", "HEAD").strip()
    lab.evidence["expected_digest"] = manifest_digest(rows)
    lab.save()
    state = candidate_state(lab, rows)
    ok = (guard["status"] == "PASS" and before["status"] == "PASS" and pre["status"] == "PASS"
          and reuse["status"] == "PASS" and not product_diff and not state["bytes_mismatch"]
          and not state["backend_frontend_staged"] and not state["tracked_diff"])
    return lab.record("LIVE-PREP", ok, {"s1_from": source, "product_diff_vs_d100ce2": product_diff, "candidate": state})


def phase_up_base(lab) -> dict:
    return gates.phase_up(lab)


def phase_provision(lab) -> dict:
    """Usuarios (personas + observador + probe) e skills. O `version_id` do `account.mark_paid` vem SO da saida
    CREATED do runbook (D-1.5-9, nunca SQL); `ja registrada` sem id conhecido falha fechado."""
    cfg, runner = lab.config, lab.runner
    out = {"users": [], "skills": []}
    entries = ([dict(p) for p in cfg["personas"]] + [dict(cfg["probe"])]
               + [{"user": E.OBSERVER, "name": driver_lab.OBSERVER_NAME, "role": driver_lab.OBSERVER_ROLE}])
    for entry in entries:
        password = lab.secrets["users"][entry["user"]]
        res = provision.create_user(runner, cfg, user=entry["user"], name=entry["name"], role=entry["role"], password=password)
        status, _ = provision.classify_user_creation(
            res.code, res.text,
            lambda e=entry, p=password: provision.verify_user(lab.client, cfg, user=e["user"], name=e["name"],
                                                              role=e["role"], password=p), entry["role"])
        out["users"].append({"user": entry["user"], "role": entry["role"], "class": status})
    res = runner.run(["docker", "exec", E.BACKEND, "python", "-m", cfg["skills"][0]["script"]])
    status, _ = provision.classify_skill_registration(
        res.code, res.text, lambda: provision.skill_catalog_ok(runner, cfg, cfg["skills"][0]["key"]))
    out["skills"].append({"key": cfg["skills"][0]["key"], "class": status})
    cap = driver_lab.capture_mark_paid_version(runner, cfg)
    out["skills"].append({"key": "account.mark_paid", "class": cap["class"], "version_id": cap["version_id"]})
    lab.evidence["mark_paid_version_id"] = cap["version_id"]
    lab.save()
    absences = provision.forbidden_absences(runner, cfg)
    ok = (all(u["class"] == provision.CREATED for u in out["users"])
          and all(s["class"] == provision.CREATED for s in out["skills"]) and all(v == 0 for v in absences.values()))
    return lab.record("LIVE-PROVISION", ok, {**out, "absences": absences})


COMPOSE_MUTATIONS = {
    "driver_network_not_internal": lambda x: x["networks"]["auneron_sim_driver"].__setitem__("internal", False),
    "driver_host_network": lambda x: x["services"]["sim-driver"].__setitem__("network_mode", "host"),
    "driver_bind_mount": lambda x: x["services"]["sim-driver"]["volumes"][0].__setitem__("type", "bind"),
    "collector_docker_sock": lambda x: x["services"]["sim-collector"]["volumes"][0].__setitem__("source", "/var/run/docker.sock"),
    "driver_extra_hosts": lambda x: x["services"]["sim-driver"].__setitem__("extra_hosts", ["host.docker.internal:host-gateway"]),
    "postgres_in_driver_net": lambda x: x["services"]["sim-postgres"]["networks"].__setitem__("auneron_sim_driver", None),
    "driver_publishes_port": lambda x: x["services"]["sim-driver"].__setitem__("ports", [{"published": "9000", "target": 9000}]),
    "driver_env_oracle": lambda x: x["services"]["sim-driver"]["environment"].__setitem__("SIM_DRIVER_ORACLE", "x"),
    "driver_on_internal_net": lambda x: x["services"]["sim-driver"]["networks"].__setitem__("auneron_sim_internal", None),
    "backend_single_net": lambda x: x["services"]["sim-backend"]["networks"].pop("auneron_sim_driver"),
}


def phase_compose_config(lab) -> dict:
    resolved = E.resolved_compose(lab)
    E.write_json(lab.live_dir / "compose_resolved.json", resolved)
    problems = driver_lab.check_resolved_driver_compose(resolved, lab.config)
    negatives = {}
    for label, mutate in COMPOSE_MUTATIONS.items():
        mutated = copy.deepcopy(resolved)
        try:
            mutate(mutated)
        except (KeyError, IndexError, TypeError) as error:
            negatives[label] = f"MUTATION_NOT_APPLIED:{error!r}"      # mutacao que nao aplica NAO conta como recusa
            continue
        negatives[label] = "REFUSED" if driver_lab.check_resolved_driver_compose(mutated, lab.config) else "NOT_REFUSED"
    networks = {n: sorted((s.get("networks") or {})) for n, s in resolved["services"].items()}
    ok = not problems and all(v == "REFUSED" for v in negatives.values())
    return lab.record("LIVE-COMPOSE-CONFIG", ok, {"problems": problems, "negatives": negatives, "networks": networks,
                                                  "resolved_sha256": E.sha(lab.live_dir / "compose_resolved.json")})


def phase_build_images(lab) -> dict:
    """Imagens do Driver/Collector a partir do INDICE (`git checkout-index`), nunca da arvore de trabalho."""
    tag, out = lab.config.image_tag, {}
    for kind, package, dockerfile in (("driver", "sim/driver", "driver.Dockerfile"),
                                      ("collector", "sim/collector", "collector.Dockerfile")):
        ctx = Path(tempfile.mkdtemp(prefix=f"sim15-ctx-{kind}-"))
        files = git(lab, "ls-files", "--cached", package).split()
        chk = lab.runner.run(["git", "-C", str(REPO_ROOT), "checkout-index", f"--prefix={ctx.as_posix()}/", "--", *files])
        same = chk.code == 0 and all(E.sha(ctx / f) == E.sha(REPO_ROOT / f) for f in files)
        image = f"{driver_lab.NEW_IMAGES[kind]}:{tag}"
        res = lab.runner.run(["docker", "build", "-f", str(REPO_ROOT / "sim" / "stack" / dockerfile), "-t", image, str(ctx)],
                             timeout=1800)
        image_id = lab.runner.run(["docker", "image", "inspect", "-f", "{{.Id}}", image]).out.strip()
        out[kind] = {"files": len(files), "index_equals_worktree": same, "build_code": res.code, "image_id": image_id,
                     "tail": res.text[-400:]}
        shutil.rmtree(ctx, ignore_errors=True)
    ok = all(v["build_code"] == 0 and v["index_equals_worktree"] and v["files"] > 0 and v["image_id"] for v in out.values())
    return lab.record("LIVE-BUILD", ok, out)


def phase_up_ext(lab) -> dict:
    since = time.time()
    res = E.compose_ext(lab, "up", "-d")
    ready = lab.wait_ready()
    started = lab.startup_record(since)
    ok = (res.code == 0 and ready["ready"] and bool(started) and started.get("environment") == "production"
          and started.get("build_git_sha") == lab.config.commit)
    return lab.record("LIVE-UP-EXT", ok, {"ready": ready, "application_started": started})


def phase_stage_base(lab) -> dict:
    """Entradas de BASE (planas) para o DR-1: Driver = agenda + manifesto + segredos; Collector = plano + segredos."""
    info = flow.prepare(lab, S.scen_dr6(lab.instance_id), S.oracle_dr6(), version_id(lab))
    flow.stage_secrets(lab)
    flow.stage(lab, info, "k", flat=True)
    return lab.record("LIVE-STAGE-BASE", True, {"plan_sha256": E.sha(info["work"] / "collection_plan.json"),
                                                "agenda_sha256": E.sha(info["work"] / "public_agenda.json")})


# ----------------------------------------------------------------------------------- DR-1
def forbidden_hashes(lab) -> dict:
    found = {}
    for root in (REPO_ROOT / "sim" / "scenarios", REPO_ROOT / "sim" / "oracle" / "artifacts"):
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "public_agenda.json":
                found[hashlib.sha256(path.read_bytes()).hexdigest()] = str(path.relative_to(REPO_ROOT))
    for path in lab.live_dir.glob("scen_*/oracle_tech.json"):
        found[hashlib.sha256(path.read_bytes()).hexdigest()] = str(path.relative_to(lab.dir))
    return found


def phase_dr1(lab) -> dict:
    forbidden = forbidden_hashes(lab)
    args = json.dumps({"forbidden_hashes": sorted(forbidden), "name_needles": list(driver_lab.PROBE_NAME_NEEDLES)})

    def probe(container, extra_args=None, env=None):
        res = E.dexec(lab, container, "python", "-", extra_args or args, stdin=driver_lab.PROBE_SCRIPT, env=env, timeout=300)
        if res.code != 0:
            raise RuntimeError(f"sonda falhou em {container}: {res.text[-400:]}")
        return json.loads(res.out.strip().splitlines()[-1])

    results = {}
    for container, expected_inputs, volumes in (
            (E.DRIVER, {"public_agenda.json", "driver_manifest.json", "secrets.json"},
             {"auneron_sim_driver_state", "auneron_sim_driver_inputs"}),
            (E.COLLECTOR, {"collection_plan.json", "secrets.json"},
             {"auneron_sim_collector_state", "auneron_sim_collector_inputs"})):
        clean = probe(container)
        failures = driver_lab.judge_probe(clean)
        listing = sorted(E.dexec(lab, container, "ls", "/inputs").out.split())
        E.dexec(lab, container, "sh", "-c", "mkdir -p /tmp/leak && : > /tmp/leak/oracle.json")
        planted = probe(container, env={"SIM_PLANTED_ORACLE": "1"})
        E.dexec(lab, container, "python", "-c", "open('/tmp/leak/y.bin','wb').write(b'planted-control')")
        control_args = json.dumps({"forbidden_hashes": [hashlib.sha256(b"planted-control").hexdigest()],
                                   "name_needles": ["oracle", "world"]})
        hashed = probe(container, extra_args=control_args)
        controls = {"name_oracle_detected": any("oracle.json" in m for m in planted["name_matches"]),
                    "env_detected": "SIM_PLANTED_ORACLE" in planted["env_findings"],
                    "hash_copy_detected": any(m.endswith("y.bin") for m in hashed["hash_matches"])}
        E.dexec(lab, container, "rm", "-rf", "/tmp/leak")
        after = driver_lab.judge_probe(probe(container))
        inspect = json.loads(lab.runner.run(["docker", "inspect", container]).out)[0]
        results[container] = {
            "probe": clean, "failures": failures, "inputs": listing, "inputs_failures": judges.judge_inputs(listing, expected_inputs),
            "controls": controls, "controls_failures": judges.judge_probe_controls(controls), "after_cleanup_failures": after,
            "topology_failures": judges.judge_topology(inspect, volumes)}
    manifest_text_ = E.dexec(lab, E.DRIVER, "cat", "/inputs/driver_manifest.json").out
    manifest_clean = (not any(m in manifest_text_.lower() for m in ("oracle", "world"))
                      and not any(h in manifest_text_ for h in forbidden))
    ok = manifest_clean and all(not (r["failures"] or r["inputs_failures"] or r["controls_failures"]
                                     or r["after_cleanup_failures"] or r["topology_failures"]) for r in results.values())
    return lab.record("DR-1", ok, {"containers": results, "manifest_clean": manifest_clean,
                                   "forbidden_hash_count": len(forbidden)})


# ----------------------------------------------------------------------------------- DR-2
def phase_dr2(lab, refs: dict | None = None, n: int = 5) -> dict:
    if refs is None:
        refs = json.loads((lab.live_dir / "scen_c" / "refs.json").read_text(encoding="utf-8"))   # dados da fase `c0`
    refusal = E.parse_last_json(E.dexec(lab, E.COLLECTOR, "python", "-", stdin=script("refusal.py")))
    refusal_failures = judges.judge_refusal(refusal)
    entries = refs["refs"]
    accounts = sorted({v["account_id"] for v in entries.values() if "account_id" in v})
    approvals = sorted({v["approval_id"] for v in entries.values() if "approval_id" in v})
    work_items = sorted({v["work_item_id"] for v in entries.values() if "work_item_id" in v})
    dues = sorted({(v["rec_ref"], v["due"]) for v in entries.values() if "due" in v and "rec_ref" in v})
    paths = ["/auth/me", "/approvals?limit=100", "/accounts/?cliente=%5BSIM-HARNESS%5D&skip=0&limit=200"]
    for a in accounts[:2]:
        paths += [f"/accounts/{a}", f"/accounts/{a}/classification", f"/brain/?account_id={a}&limit=500&skip=0",
                  f"/memories?scope_type=account&account_id={a}&memory_key=client_behavior_payment_pattern&status=active&limit=100",
                  f"/work-items?scope_type=account&account_id={a}&limit=100"]
    paths += [f"/approvals/{a}" for a in approvals[:2]]
    for w in work_items[:1]:
        paths += [f"/work-items/{w}", f"/work-items/{w}/escalation-observations?limit=100"]
    for rec, due in dues:
        acc = entries.get(rec, {}).get("account_id")
        if acc:
            paths += [f"/outcomes/accounts/{acc}/episodes/{due}", f"/recommendations/mark-overdue/accounts/{acc}/episodes/{due}",
                      f"/recommendations/human-escalation/accounts/{acc}/episodes/{due}"]
    paths = list(dict.fromkeys(paths))
    from datetime import timezone

    now = lab.clock.expected_now().astimezone(timezone.utc).isoformat()
    email = lab.config.email(E.OBSERVER)
    reader = script("reader.py")
    login = E.dexec(lab, E.COLLECTOR, "python", "-", json.dumps({"paths": [], "n": 0, "email": email, "now": now}),
                    stdin=reader, timeout=300)             # login ANTES da janela de medida
    if login.code != 0:
        raise RuntimeError(login.text[-300:])
    time.sleep(2)
    snap_a = fingerprint(lab.runner, lab.config)
    time.sleep(10)
    snap_b = fingerprint(lab.runner, lab.config)
    baseline = delta(snap_a, snap_b)                       # ruido dos workers numa janela SEM leituras
    before = fingerprint(lab.runner, lab.config)
    res = E.dexec(lab, E.COLLECTOR, "python", "-", json.dumps({"paths": paths, "n": n, "email": email, "now": now}),
                  stdin=reader, timeout=900)
    after = fingerprint(lab.runner, lab.config)
    reads = E.parse_last_json(res)
    read_delta = delta(before, after)
    identical = all(len(set(h)) == 1 for h in reads["hashes"].values())
    all_200 = all(set(s) == {200} for s in reads["statuses"].values())
    purity_failures = judges.judge_purity(baseline=baseline, read_delta=read_delta, identical=identical, all_200=all_200,
                                          requests=reads["requests"], tables=len(before))
    # controle POSITIVO: a instrumentacao DETECTA uma leitura que escreve (NBA)
    coord = lab.login("sim-coordenador-fin")
    target = next(((entries[v["rec_ref"]]["account_id"], v["due"]) for v in entries.values() if v.get("kind") == "mark_overdue"), None)
    if target is None:
        raise GuardViolation("DR-2: sem episodio mark_overdue nas refs para o controle positivo")
    time.sleep(1)
    c_before = fingerprint(lab.runner, lab.config)
    nba = coord.request("GET", f"/recommendations/next-best-action/accounts/{target[0]}/episodes/{target[1]}")
    positive = delta(c_before, fingerprint(lab.runner, lab.config))
    positive_failures = judges.judge_positive_control(nba.status, positive)
    ok = not (refusal_failures or purity_failures or positive_failures)
    return lab.record("DR-2", ok, {
        "refusal": refusal, "refusal_failures": refusal_failures, "paths": len(paths), "n": n, "requests": reads["requests"],
        "instrument": "content fingerprint (count+md5) of every public table; pg_stat rejected (blind to pooled writers)",
        "tables_fingerprinted": len(before), "baseline_delta": baseline, "read_delta": read_delta,
        "purity_failures": purity_failures, "positive_control_delta": positive, "positive_failures": positive_failures})


# ----------------------------------------------------------------------------------- DR-3
def run_slot(lab, env: dict, day: int, at: str, clock: bool = True) -> tuple:
    if clock:
        lab.clock.set(lab.clock.virtual(day, at))
    res = E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "run-slot", "--day", str(day), "--at", at, env=env)
    return res.code, E.parse_last_json(res)


def evidence_lines(lab, tag: str) -> list:
    return E.cat_json_lines(lab, E.DRIVER, f"/state/{tag}/driver_evidence.jsonl")


def phase_dr3(lab) -> dict:
    n = version_id(lab)
    out = {"captured_version_id": n, "captured_from": "saida CREATED do runbook (sem SQL)"}
    try:
        driver_lab.capture_mark_paid_version(lab.runner, lab.config)
        out["rerun_without_known_id"] = "NOT_REFUSED"
    except GuardViolation as error:
        out["rerun_without_known_id"] = f"REFUSED: {error}"
    known = driver_lab.capture_mark_paid_version(lab.runner, lab.config, known_version_id=n)
    out["rerun_with_manifest_id"] = {"class": known["class"], "version_id": known["version_id"]}
    day = S.DAYS["dr3"]
    pos = flow.prepare(lab, S.scen_dr3(lab.instance_id, "h", day), None, n)
    env = flow.stage(lab, pos, "h")["driver"]
    codes = [run_slot(lab, env, day, at) for at in ("09:00", "09:30")]
    req = [r for r in evidence_lines(lab, "h") if r.get("kind") == "http" and r["op"] == "request_mark_paid"]
    body = (req[-1]["response"] or {}).get("request", {}) if req else {}
    out["positive"] = {"slot_codes": [c for c, _ in codes], "http_status": req[-1]["status"] if req else None,
                       "skill_version_id": body.get("skill_version_id"), "skill_key": body.get("skill_key")}
    neg = flow.prepare(lab, S.scen_dr3(lab.instance_id, "h2", day), None, 1)       # 1 = account.mark_overdue (errado de proposito)
    env2 = flow.stage(lab, neg, "h2")["driver"]
    codes2 = [run_slot(lab, env2, day, at, clock=False) for at in ("09:00", "09:30")]
    req2 = [r for r in evidence_lines(lab, "h2") if r.get("kind") == "http" and r["op"] == "request_mark_paid"]
    out["negative"] = {"slot_codes": [c for c, _ in codes2], "payload": [p for _, p in codes2],
                       "request_http": [(r["status"], {k: ((r["response"] or {}).get("request") or {}).get(k)
                                                       for k in ("skill_version_id", "skill_key")}) for r in req2],
                       "fail_closed": codes2[-1][0] == 3 or bool(req2 and all(r["status"] >= 400 for r in req2))}
    failures = judges.judge_dr3(out, n) + ([] if all(c == 0 for c, _ in codes) else ["slot do cenario positivo falhou"])
    return lab.record("DR-3", not failures, {**out, "failures": failures})


# ----------------------------------------------------------------------------------- DR-4
def phase_dr4(lab) -> dict:
    day, persona = S.DAYS["dr4"], "sim-faturamento"
    email = lab.config.email(persona)
    read_session = ("import sqlite3,json,hashlib;c=sqlite3.connect('file:/state/e/driver_state.db?mode=ro',uri=True);"
                    "r=c.execute(\"select cookie,expires_at from sessions where persona='sim-faturamento'\").fetchone();"
                    "print(json.dumps(None if r is None else {'cookie_sha':hashlib.sha256(r[0].encode()).hexdigest()[:12],'expires_at':r[1]}))")
    tamper = ("import sqlite3;c=sqlite3.connect('/state/e/driver_state.db');"
              "c.execute(\"update sessions set cookie='sim_session=INVALID-COOKIE-INJECTED', expires_at='2099-01-01T00:00:00Z' "
              "where persona='sim-faturamento'\");c.commit();print('tampered')")

    def session_state():
        return E.parse_last_json(E.dexec(lab, E.DRIVER, "python", "-c", read_session))

    def product_sessions():
        return int(sql(lab, f"select count(*) from auth_sessions where user_id=(select id from users where email='{email}')")[0][0])

    info = flow.prepare(lab, S.scen_dr4(lab.instance_id, "e", day), None, version_id(lab))
    env = flow.stage(lab, info, "e")["driver"]
    steps = {}
    c1 = run_slot(lab, env, day, "09:00")
    s1 = session_state()
    steps["1_initial"] = {"code": c1[0], "session": s1, "product_sessions": product_sessions()}
    c2 = run_slot(lab, env, day, "16:40")                      # faltam ~20 min virtuais: re-login PROATIVO
    s2 = session_state()
    http2 = [r["status"] for r in evidence_lines(lab, "e") if r.get("kind") == "http" and r["seq"] == 2]
    steps["2_proactive"] = {"code": c2[0], "session": s2, "http_statuses_seq2": http2, "product_sessions": product_sessions(),
                            "relogged": s2["cookie_sha"] != s1["cookie_sha"] and s2["expires_at"] > s1["expires_at"]}
    E.dexec(lab, E.DRIVER, "python", "-c", tamper)             # cookie nao reconhecido pelo produto
    c3 = run_slot(lab, env, day, "16:50")
    s3 = session_state()
    http3 = [r["status"] for r in evidence_lines(lab, "e") if r.get("kind") == "http" and r["seq"] == 3]
    steps["3_401_relogin"] = {"code": c3[0], "session": s3, "http_statuses_seq3": http3, "product_sessions": product_sessions(),
                              "relogged": s3["cookie_sha"] != s2["cookie_sha"] and s3["expires_at"] < "2099"}
    c4 = run_slot(lab, env, day + 1, "09:00")                  # apos a expiracao VIRTUAL
    s4 = session_state()
    lines = evidence_lines(lab, "e")
    http4 = [r["status"] for r in lines if r.get("kind") == "http" and r["seq"] == 4]
    steps["4_expired"] = {"code": c4[0], "session": s4, "http_statuses_seq4": http4, "product_sessions": product_sessions(),
                          "relogged": s4["cookie_sha"] != s3["cookie_sha"]}
    results = [r for r in lines if r.get("kind") == "op_result"]
    failures = judges.judge_dr4(steps)
    if len(results) != 4 or not all(r["http_status"] in (200, 201) for r in results):
        failures.append("as 4 ops nao terminaram com 200/201")
    return lab.record("DR-4", not failures, {**steps, "failures": failures})


# ----------------------------------------------------------------------------------- DR-5
def phase_dr5(lab) -> dict:
    day = S.DAYS["dr5"]
    info = flow.prepare(lab, S.scen_dr5(lab.instance_id, "f", day), None, version_id(lab))
    env = flow.stage(lab, info, "f")["driver"]
    lab.clock.set(lab.clock.virtual(day, "09:00"))
    code = script("dr5_cases.py").replace("__DAY__", str(day))
    results = {}
    for case in range(4):
        res = E.dexec(lab, E.DRIVER, "python", "-", str(case), stdin=code, env=env, timeout=300)
        results[case] = E.parse_last_json(res) if res.code == 0 else {"execution_error": res.text[-600:]}
    checks = judges.judge_dr5_run(results)
    return lab.record("DR-5", all(checks.values()), {"checks": checks, "cases": results})


# ----------------------------------------------------------------------------------- DR-6
def phase_dr6(lab) -> dict:
    """Pipeline completo EM CONTAINERS (entradas planas do `stage_base`) com a barreira diaria real."""
    day = S.DAYS["dr6"]
    work = lab.live_dir / "scen_k"
    info = {"work": work, "plan": json.loads((work / "collection_plan.json").read_text(encoding="utf-8")),
            "ops": json.loads((work / "public_agenda.json").read_text(encoding="utf-8"))["ops"]}
    oracle = json.loads((work / "oracle_tech.json").read_text(encoding="utf-8"))
    run = flow.run_pipeline(lab, info, oracle, "k", first_day=day, last_day=day, flat=True)
    lines, refs = run["driver_lines"], run["refs"]
    per_ref = {}
    for r in lines:
        if r.get("kind") == "op_result" and r["op"] == "execute_mark_paid":
            per_ref.setdefault(r["ref"], []).append({"seq": r["seq"], "http": r["http_status"], "duplicate": r.get("duplicate")})
    conc = [r for r in lines if r.get("kind") == "concurrency"]
    db = {}
    for ref, entry in refs["refs"].items():
        if entry.get("kind") == "mark_paid":
            acc, appr = refs["refs"][entry["rec_ref"]]["account_id"], entry["approval_id"]
            db[ref] = {"account_id": acc, "approval_id": appr,
                       "account_events_pago": int(sql(lab, f"select count(*) from account_events where account_id = {acc} and new_status = 'pago'")[0][0]),
                       "approval_consumptions": int(sql(lab, f"select count(*) from approval_consumptions where approval_request_id = {appr}")[0][0]),
                       "account_status": sql(lab, f"select status from accounts where id = {acc}")[0][0]}
    copies = {"MP-k1": 2, "MP-k2": 3}
    failures = judges.judge_dr6(per_ref=per_ref, db=db, copies=copies, concurrency=conc)
    ev = run["evaluation"]
    if not ev["run_valid"] or ev["summary"].get("DIVERGENCE"):
        failures.append(f"avaliacao: {ev['summary']}")
    if run["problems"] or run["missing"] or len(run["collected"]) != len(http_items(info["plan"])):
        failures.append("coleta incompleta ou fora do momento seguro")
    return lab.record("DR-6", not failures, {
        "failures": failures, "per_ref": per_ref, "db": db, "evaluation_summary": ev["summary"], "seconds": run["seconds"],
        "distinction": run["distinction"], "barriers": run["state"]["barriers"], "refs_sha256": hashlib.sha256(
            json.dumps(refs, sort_keys=True).encode()).hexdigest()})


# ----------------------------------------------------------------------------------- DR-7
def phase_dr7(lab) -> dict:
    ttl = flow.o1_ttl_minutes(lab)
    from sim.driver.params import load_params

    limit = float(load_params()["o1_max_skew_s"])
    if limit != 1.0:
        raise GuardViolation("o limite congelado de O-1 (1 s) mudou no candidato")
    samples = [flow.o1_measure(lab, ttl, "baseline_no_jump")]
    day0 = S.DAYS["dr7"]
    for day, at in ((day0, "08:00"), (day0, "11:00"), (day0, "14:00"), (day0, "18:00"), (day0 + 1, "08:00"),
                    (day0 + 1, "12:30"), (day0 + 1, "18:00")):
        lab.clock.set(lab.clock.virtual(day, at))
        time.sleep(1.5)
        samples.append(flow.o1_measure(lab, ttl, f"D{day} {at} +1.5s_after_set"))
        time.sleep(20)
        samples.append(flow.o1_measure(lab, ttl, f"D{day} {at} +22s_idle"))
    for i in range(4):
        time.sleep(7)
        samples.append(flow.o1_measure(lab, ttl, f"steady_{i}"))
    judge = driver_lab.o1_judge([{k: s[k] for k in ("app_minus_db_s", "app_minus_target_s", "db_minus_target_s")} for s in samples], limit)
    per = {k: max(abs(s[k]) for s in samples) for k in ("app_minus_db_s", "app_minus_target_s", "db_minus_target_s")}
    return lab.record("DR-7", judge["status"] == "OK", {"judge": judge, "ttl_minutes": ttl, "samples": samples,
                                                        "max_abs_per_measure": per, "threshold_status": judge["status"]})


# ----------------------------------------------------------------------------------- DR-8
def phase_dr8(lab) -> dict:
    day = S.DAYS["dr8"]
    n = version_id(lab)
    info = flow.prepare(lab, S.scen_dr8(lab.instance_id, "g", day), None, n)
    env = flow.stage(lab, info, "g")["driver"]
    crash_script = script("dr8_crash.py")
    sqlite_state = ("import sqlite3,json;c=sqlite3.connect('file:/state/g/driver_state.db?mode=ro',uri=True);"
                    "print(json.dumps({'ops':c.execute('select seq,op,status from ops order by seq').fetchall(),"
                    "'refs':{k:json.loads(v) for k,v in c.execute('select ref,value from refs')}}))")

    def state():
        return E.parse_last_json(E.dexec(lab, E.DRIVER, "python", "-c", sqlite_state))

    def accounts(i):
        return int(sql(lab, f"select count(*) from accounts where cliente = '[SIM-HARNESS] g-{i} {lab.instance_id}'")[0][0])

    def crash(at, pattern, clock=True):
        if clock:
            lab.clock.set(lab.clock.virtual(day, at))
        return E.dexec(lab, E.DRIVER, "python", "-", str(day), at, pattern, stdin=crash_script, env=env, timeout=300).code

    steps = {}
    code1 = crash("09:00", CRASH_AFTER_CREATE)
    st1 = state()
    steps["crash1"] = {"exit_code": code1, "accounts_g1_in_product": accounts(1), "accounts_g2": accounts(2),
                       "driver_ops": st1["ops"], "in_flight": [o for o in st1["ops"] if o[2] == "sent"]}
    rs = lab.runner.run(["docker", "restart", E.DRIVER], timeout=120)
    time.sleep(2)
    steps["restart1"] = {"code": rs.code, "ops_after_restart": state()["ops"]}
    c, payload = run_slot(lab, env, day, "09:00", clock=False)
    steps["resume1"] = {"exit_code": c, "payload": payload, "accounts_g1": accounts(1), "accounts_g2": accounts(2),
                        "ops": state()["ops"]}
    run_slot(lab, env, day, "09:30")
    code2 = crash("09:45", CRASH_AFTER_DECISION)
    appr = state()["refs"]["MP-g1"]["approval_id"]
    decisions = lambda: int(sql(lab, f"select count(*) from approval_decisions where approval_request_id = {appr}")[0][0])  # noqa: E731
    status_in_product = sql(lab, f"select status from approval_requests where id = {appr}")[0][0]
    decisions_before = decisions()
    kill = lab.runner.run(["docker", "kill", E.DRIVER])
    start = lab.runner.run(["docker", "start", E.DRIVER])
    time.sleep(2)
    steps["crash2"] = {"exit_code": code2, "approval_status_in_product": status_in_product,
                       "decisions_before_resume": decisions_before, "kill": kill.code, "start": start.code}
    c3, p3 = run_slot(lab, env, day, "09:45", clock=False)
    steps["resume2"] = {"exit_code": c3, "payload": p3, "decisions_after_resume": decisions()}
    c4, _ = run_slot(lab, env, day, "10:00")
    acc1 = state()["refs"]["G-01"]["account_id"]
    steps["final"] = {
        "exit_code": c4,
        "status": E.parse_last_json(E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "status", env=env)),
        "account_g1_status": sql(lab, f"select status from accounts where id = {acc1}")[0][0],
        "events_pago": int(sql(lab, f"select count(*) from account_events where account_id = {acc1} and new_status = 'pago'")[0][0]),
        "consumptions": int(sql(lab, f"select count(*) from approval_consumptions where approval_request_id = {appr}")[0][0]),
        "decisions": decisions(), "accounts_g1": accounts(1), "accounts_g2": accounts(2)}
    steps["reconciled_seqs"] = sorted(r["seq"] for r in evidence_lines(lab, "g")
                                      if r.get("kind") == "op_result" and r.get("outcome") == "reconciled")
    failures = judges.judge_dr8(steps)
    return lab.record("DR-8", not failures, {**steps, "failures": failures})


# ----------------------------------------------------------------------------------- C0 e C0b
def phase_c0(lab) -> dict:
    """LIVE-C0 EM CONTAINERS: 2 dias, barreira diaria real em ambos, grupos concorrentes."""
    day = S.DAYS["c0"]
    info = flow.prepare(lab, S.scen_c0(lab.instance_id, "c", day), S.oracle_c0("c", day), version_id(lab))
    flow.stage(lab, info, "c")
    run = flow.run_pipeline(lab, info, S.oracle_c0("c", day), "c", first_day=day, last_day=day + 1)
    return _record_pipeline(lab, "LIVE-C0", run, info)


def phase_c0b(lab) -> dict:
    """LIVE-C0b: floor (D14, procedimento real, com o override) -> observed facts -> leitura publica -> proveniencia."""
    floor = S.FLOOR_DAY
    oracle = S.oracle_c0b("m", floor)
    info = flow.prepare(lab, S.scen_c0b(lab.instance_id, "m", floor), oracle, version_id(lab))
    flow.stage(lab, info, "m")
    run = flow.run_pipeline(lab, info, oracle, "m", first_day=floor - 1, last_day=floor, floor=True, with_provenance=True)
    record = _record_pipeline(lab, "LIVE-C0b", run, info, extra={
        "floor": {k: run["state"]["floor"].get(k) for k in ("preflight", "application_started", "networks_after_recreate")}
        if run["state"].get("floor") else None,
        "provenance_exit": run["facts"].get("provenance_exit"), "provenance_report": run["facts"].get("provenance_report"),
        "observations_collected": _observation_rows(run["collected"])})
    return record


def _observation_rows(collected: list) -> list:
    rows = []
    for record in collected:
        if record.get("route") == "observations":
            for obs in (record.get("body") or {}).get("items", []):
                rows.append({"path": record["path"].split("?")[0], **{k: obs.get(k) for k in sorted(obs)}})
    return rows


def _record_pipeline(lab, gate: str, run: dict, info: dict, extra: dict | None = None) -> dict:
    ev = run["evaluation"]
    ok = (ev["run_valid"] and not run["problems"] and not run["missing"] and not ev["summary"].get("HARNESS_ERROR")
          and len(run["collected"]) == len(http_items(info["plan"])))
    return lab.record(gate, ok, {
        "summary": ev["summary"], "run_valid": ev["run_valid"], "evaluation_sha256": ev["evaluation_sha256"],
        "distinction": run["distinction"], "problems": run["problems"], "missing": run["missing"],
        "divergences": [r for r in ev["results"] if r["class"] in ("DIVERGENCE", "PRODUCT_FINDING")],
        "known_limits": [r for r in ev["results"] if r["class"] == "KNOWN_LIMIT"],
        "abstentions": [r for r in ev["results"] if r["class"] == "CORRECT_ABSTENTION"],
        "seconds": run["seconds"], "barriers": run["state"]["barriers"], "provenance": ev.get("provenance"),
        "plan_sha256": E.sha(info["work"] / "collection_plan.json"), "agenda_sha256": E.sha(info["work"] / "public_agenda.json"),
        **(extra or {})})


# ----------------------------------------------------------------------------------- D90 (restart + Collector)
def phase_d90(lab) -> dict:
    """Perna curta do D90 pelo caminho VERSIONADO: restart real do backend (fail-closed), duas redes, identidade de build,
    barreira extraordinaria, coletas `requires: after_restart_barrier` e `pre/post_slot` do slot do restart, continuacao
    do Driver e avaliacao offline. `after_restart_barrier` e barreira, nunca `safe_at` de coleta (F-D90-2)."""
    day, tag = S.RESTART_DAY, "r"
    oracle = S.oracle_d90(tag, day)
    info = flow.prepare(lab, S.scen_d90(lab.instance_id, tag, day), oracle, version_id(lab))
    flow.stage(lab, info, tag)
    denv, cenv = flow.driver_env(tag), flow.collector_env(tag)

    def collector(*args):
        return E.dexec(lab, E.COLLECTOR, "python", "-m", "sim.collector.cli", *args, env=cenv)

    def early_control() -> dict:
        """Coleta ANTECIPADA: os itens do dia exigem a barreira do restart, que ainda nao ocorreu => recusa, 0 coletas."""
        before = E.parse_last_json(collector("status"))
        early = collector("trigger", "--kind", "after_daily_barrier", "--day", str(day), "--at", "18:00")
        after = E.parse_last_json(collector("status"))
        return {"exit_code": early.code, "payload": E.parse_last_json(early), "collected_before": before.get("collected"),
                "collected_after": after.get("collected")}

    run = flow.run_pipeline(lab, info, oracle, tag, first_day=day, last_day=day, before_restart=early_control)
    second = E.parse_last_json(collector("trigger", "--kind", "after_daily_barrier", "--day", str(day), "--at", "18:00"))
    dstatus = E.parse_last_json(E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "status", env=denv))
    report = judges.build_d90_report(plan=info["plan"], run=run, second_trigger=second, driver_status=dstatus, day=day,
                                     at=S.RESTART_AT)
    failures = judges.judge_d90(report)
    E.write_json(lab.live_dir / "d90_report.json", report)
    return lab.record("D90", not failures, {
        "failures": failures, "seconds": run["seconds"], "restart": report["restart"], "times": report["times"],
        "evaluation_summary": run["evaluation"]["summary"], "distinction": run["distinction"],
        "results": [{k: r[k] for k in ("exp_id", "kind", "evidence_class", "class")} for r in run["evaluation"]["results"]],
        "retrigger": report["retrigger"], "driver": report["driver"],
        "session_401_seqs": [r["seq"] for r in run["driver_lines"] if r.get("kind") == "http" and r["status"] == 401]})


# ----------------------------------------------------------------------------------- teardown
def phase_reset(lab) -> dict:
    """Destroi o stack descartavel (down -v, so o prefixo do lab) entre as duas sessoes. Mesma instancia, mesmas imagens."""
    down = E.compose_ext(lab, "down", "-v", "--remove-orphans")
    for key in ("floor_t0",):
        lab.evidence.pop(key, None)
    state = lab.dir / "clock_state.json"
    if state.exists():
        state.unlink()
    leftovers = lab.runner.run(["docker", "ps", "-a", "--filter", "name=auneron-sim", "-q"]).out.strip()
    return lab.record("LIVE-RESET", down.code == 0 and not leftovers, {"down_code": down.code})


def collect_container_state(lab) -> dict:
    """Preserva a evidencia que vive nos volumes (estado e evidencia do Driver e do Collector) antes do `down -v`."""
    out = {}
    for container, name in ((E.DRIVER, "driver"), (E.COLLECTOR, "collector")):
        target = lab.live_dir / "container_state" / name
        if target.exists():
            shutil.rmtree(target)
        res = E.cp_out(lab, container, "/state", target)
        out[name] = {"code": res.code, "files": sum(1 for p in target.rglob("*") if p.is_file()) if target.exists() else 0}
    return out


def phase_teardown(lab) -> dict:
    approved = [tuple(r) for r in lab.evidence.get("approved_manifest", [])] or None
    expected_digest = lab.evidence.get("expected_digest")
    preserved = collect_container_state(lab)
    down = E.compose_ext(lab, "down", "-v", "--remove-orphans")
    r, cfg = lab.runner, lab.config
    containers = r.run(["docker", "ps", "-a", "--filter", "name=auneron-sim", "--format", "{{.Names}}"]).out.split()
    volumes = [v for v in r.run(["docker", "volume", "ls", "-q"]).out.split() if v.startswith("auneron_sim")]
    networks = [n for n in r.run(["docker", "network", "ls", "--format", "{{.Name}}"]).out.split()
                if n.startswith(("auneron_sim", "auneron-sim"))]
    images = [i.split("|")[0] for i in r.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}|{{.ID}}"]).out.split()
              if i.startswith("auneron-sim-")]
    expected_images = [cfg.image(k) for k in ("base", "backend", "postgres")] + [
        f"{v}:{cfg.image_tag}" for v in driver_lab.NEW_IMAGES.values()]
    ancestors = {i: r.run(["docker", "ps", "-a", "--filter", f"ancestor={i}", "--format", "{{.Names}}"]).out.split()
                 for i in expected_images}
    guardprobe = r.run(["docker", "ps", "-a", "--filter", "name=auneron-sim-guardprobe", "-q"]).out.strip()
    snap = gates.phase_snapshot(lab, "after")
    state = candidate_state(lab, approved)
    data = {"containers": containers, "volumes": volumes, "networks": networks, "images": images,
            "expected_images": expected_images, "containers_from_lab_images": ancestors, "guardprobe_leftover": bool(guardprobe),
            "head": state["head"], "expected_head": lab.evidence.get("expected_head", state["head"]),
            "staged_count": len(state["staged"]), "expected_staged_count": len(approved) if approved is not None else len(state["staged"]),
            "tracked_diff": state["tracked_diff"], "bytes_mismatch": state["bytes_mismatch"], "digest": state["digest"],
            "expected_digest": expected_digest or state["digest"], "extra_untracked": state["extra_untracked"],
            "sim14_hash_mismatches": state["sim14_hash_mismatches"], "dev_snapshot": snap["status"],
            "secrets_outside_repo": REPO_ROOT.resolve() not in lab_home().parents}
    failures = judges.judge_teardown(data)
    if down.code != 0:
        failures.append(f"compose down: {down.code}")
    return lab.record("LIVE-TEARDOWN", not failures, {**data, "down_code": down.code, "failures": failures,
                                                      "container_state_preserved": preserved,
                                                      "dev_snapshot_diffs": {k: snap.get(k) for k in ("diff_vs_before", "diff_vs_pre_canonical")}})
