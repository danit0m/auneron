"""
Juizes PUROS do harness live (sem Docker): cada criterio de PASS/FAIL dos gates mora aqui e e testado
estaticamente com casos positivos E negativos. Os defeitos do PRE-LIVE (topologia vacuosa, instrumento cego,
criterio de nao rastreados por igualdade) viram testes de regressao.
"""

from __future__ import annotations

PRODUCT_FACT = "PRODUCT_FACT"
EVALUATOR_DERIVATION = "EVALUATOR_DERIVATION"
DRIVER_EVIDENCE = "DRIVER_EXECUTION_EVIDENCE"
INSTRUMENT_FAILURE = "HARNESS_ERROR"
EXPECTED_DRIVER_NETWORK = "auneron_sim_driver"
CAPTURE_SKIPPED_NETWORK_MODES = ("host", "none", "default", "bridge")


# ------------------------------------------------------------------------------------ DR-1
def is_named_volume_bind(bind: str) -> bool:
    """`nome:/destino[:modo]` e volume NATIVO; `/caminho:...` e `C:\\...:...` sao bind mount."""
    if ":" not in bind:
        return False
    source = bind.split(":", 1)[0]
    return bool(source) and not source.startswith(("/", "\\", ".", "~")) and not (len(bind) > 1 and bind[1] == ":")


def judge_topology(inspect: dict, expected_volumes: set) -> list:
    """Lista de falhas (vazia = PASS) a partir do `docker inspect` de um container do Driver/Collector."""
    failures = []
    host = inspect.get("HostConfig", {})
    networks = sorted((inspect.get("NetworkSettings", {}).get("Networks") or {}))
    if networks != [EXPECTED_DRIVER_NETWORK]:
        failures.append(f"redes {networks}")
    mounts = inspect.get("Mounts") or []
    if not mounts:
        failures.append("sem montagens (o estado e as entradas ficam em volumes nativos)")
    if any(m.get("Type") != "volume" for m in mounts):
        failures.append(f"montagem nao nativa: {[m.get('Type') for m in mounts]}")
    names = {m.get("Name") for m in mounts}
    if names != set(expected_volumes):
        failures.append(f"volumes {sorted(n for n in names if n)} != {sorted(expected_volumes)}")
    binds = host.get("Binds") or []
    if any(not is_named_volume_bind(b) for b in binds):
        failures.append(f"bind mount: {binds}")
    if host.get("NetworkMode") in CAPTURE_SKIPPED_NETWORK_MODES:
        failures.append(f"network_mode {host.get('NetworkMode')}")
    if host.get("Privileged") is not False:
        failures.append("privilegiado")
    ports = [p for p in ((inspect.get("NetworkSettings", {}).get("Ports")) or {}).values() if p]
    if ports:
        failures.append(f"portas publicadas: {ports}")
    if host.get("CapAdd"):
        failures.append(f"cap_add {host['CapAdd']}")
    if host.get("PidMode") not in ("", None):
        failures.append(f"pid_mode {host['PidMode']}")
    if (inspect.get("Config", {}).get("User") or "") in ("", "root", "0"):
        failures.append("container roda como root")
    return failures


def judge_inputs(listing: list, expected: set) -> list:
    return [] if set(listing) == set(expected) else [f"/inputs {sorted(listing)} != {sorted(expected)}"]


def judge_probe_controls(controls: dict) -> list:
    """A sonda precisa ACUSAR os 3 vazamentos plantados; senao um `PASS` limpo nao prova nada."""
    wanted = ("name_oracle_detected", "env_detected", "hash_copy_detected")
    return [f"controle positivo nao detectado: {k}" for k in wanted if not controls.get(k)]


# ------------------------------------------------------------------------------------ DR-2
def judge_refusal(result: dict) -> list:
    failures = [f"nao recusado: {k}" for k, v in result.get("cases", {}).items() if v != "REFUSED"]
    if not result.get("cases"):
        failures.append("sem casos")
    if result.get("wrong_host") != "REFUSED":
        failures.append("host errado aceito")
    if result.get("network_calls") != 0:
        failures.append(f"chamadas de rede: {result.get('network_calls')}")
    if result.get("mutating_attrs"):
        failures.append(f"atributos mutantes: {result['mutating_attrs']}")
    return failures


def judge_purity(*, baseline: dict, read_delta: dict, identical: bool, all_200: bool, requests: int,
                 tables: int, min_requests: int = 150, min_tables: int = 20) -> list:
    failures = []
    changed = sorted(t for t in read_delta if t not in baseline)      # o que NAO e ruido de worker
    if changed:
        failures.append(f"tabelas alteradas por leituras: {changed}")
    if not identical:
        failures.append("respostas diferentes entre repeticoes")
    if not all_200:
        failures.append("status diferente de 200")
    if requests < min_requests:
        failures.append(f"poucas leituras: {requests}")
    if tables < min_tables:
        failures.append(f"instrumento viu poucas tabelas: {tables}")
    return failures


def judge_positive_control(status: int, delta: dict, table: str = "nba_recommendation_snapshots") -> list:
    """A instrumentacao PRECISA detectar uma leitura que escreve (NBA): +1 linha em `table`."""
    entry = delta.get(table)
    if status != 200:
        return [f"controle positivo: status {status}"]
    if entry is None or entry["count"][1] != entry["count"][0] + 1:
        return ["instrumento NAO detectou a escrita do controle positivo"]
    return []


# ------------------------------------------------------------------------------------ DR-3..DR-8
def judge_dr3(out: dict, version_id: int) -> list:
    failures = []
    if not str(out.get("rerun_without_known_id", "")).startswith("REFUSED"):
        failures.append("runbook ja registrado sem id conhecido nao falhou fechado")
    if out.get("rerun_with_manifest_id", {}).get("version_id") != version_id:
        failures.append("id do manifesto nao devolvido")
    pos = out.get("positive", {})
    if not (pos.get("http_status") == 201 and pos.get("skill_version_id") == version_id
            and pos.get("skill_key") == "account.mark_paid"):
        failures.append(f"resposta incoerente: {pos}")
    neg = out.get("negative", {})
    if not neg.get("fail_closed"):
        failures.append("manifesto errado nao falhou fechado")
    return failures


def judge_dr4(steps: dict) -> list:
    failures = []
    if not (steps["2_proactive"]["relogged"] and steps["2_proactive"]["http_statuses_seq2"] == [200]):
        failures.append("re-login proativo (sem 401) nao comprovado")
    if not (steps["3_401_relogin"]["relogged"] and steps["3_401_relogin"]["http_statuses_seq3"] == [401, 200]):
        failures.append("re-login por 401 nao comprovado")
    if not (steps["4_expired"]["relogged"] and steps["4_expired"]["http_statuses_seq4"] == [200]):
        failures.append("re-login apos expiracao virtual nao comprovado")
    if not all(steps[k]["code"] == 0 for k in ("1_initial", "2_proactive", "3_401_relogin", "4_expired")):
        failures.append("slot com exit code diferente de 0")
    return failures


def judge_dr5(results: dict) -> dict:
    """{caso: bool} para os 4 casos de reconciliacao de `create_receivable`."""
    def reads(case):
        return [r for r in results[case]["evidence"] if r.get("kind") == "reconcile_read"]

    def final(case):
        return next((r for r in results[case]["evidence"] if r.get("kind") == "op_result"), {})

    c0, c1, c2, c3 = (results.get(i, {}) for i in range(4))
    return {
        "case0_empty_x3_then_single_resend": (c0.get("result") == "completed" and len(reads(0)) == 3
                                              and all(r["candidates"] == [] for r in reads(0))
                                              and final(0).get("resent_after_stable_absence") is True
                                              and c0.get("accounts_after") == 1 and c0.get("seconds", 0) >= 10),
        "case1_adopt_no_duplicate": (c1.get("result") == "completed" and len(reads(1)) == 3
                                     and len({tuple(r["candidates"]) for r in reads(1)}) == 1
                                     and len(reads(1)[0]["candidates"]) == 1 and final(1).get("outcome") == "reconciled"
                                     and c1.get("accounts_after") == 1),
        "case2_two_candidates_harness_error": (c2.get("result") == "HARNESS_ERROR" and "2 candidatos" in c2.get("error", "")
                                               and c2.get("accounts_after") == 2 and c2.get("op_status") == "failed"),
        "case3_unstable_reads_harness_error": (c3.get("result") == "HARNESS_ERROR" and "instavel" in c3.get("error", "")
                                               and c3.get("accounts_after") == 1 and c3.get("op_status") == "failed"),
    }


def judge_dr5_run(results: dict) -> dict:
    """Falha de EXECUCAO (exit code != 0 do container) e distinta do resultado do caso: os casos 2 e 3 trazem
    legitimamente a chave `error` (a mensagem do HarnessError esperado). Regressao do DR-5 (sessao 2, tentativa 1)."""
    if len(results) != 4 or any("execution_error" in r for r in results.values()):
        return {"execution": False}
    return judge_dr5(results)


def judge_dr6(*, per_ref: dict, db: dict, copies: dict, concurrency: list) -> list:
    failures = []
    for ref, expected in copies.items():
        results = per_ref.get(ref, [])
        if len(results) != expected:
            failures.append(f"{ref}: {len(results)} respostas, esperado {expected}")
        if sum(1 for x in results if x["http"] == 200 and not x["duplicate"]) != 1:
            failures.append(f"{ref}: nao ha exatamente 1 resposta nao duplicada")
        if any(not ((x["http"] == 200 and x["duplicate"]) or x["http"] == 409) for x in results if x["duplicate"] is not False):
            failures.append(f"{ref}: resposta duplicada fora de 200/duplicate ou 409")
        row = db.get(ref, {})
        if (row.get("account_events_pago"), row.get("approval_consumptions"), row.get("account_status")) != (1, 1, "pago"):
            failures.append(f"{ref}: efeito no produto {row}")
    if len(concurrency) != len(copies) or not all(
            len(c["seqs"]) in (2, 3) and max(c["t_start"]) < min(c["t_end"]) for c in concurrency):
        failures.append("concorrencia real nao comprovada (sobreposicao das threads)")
    return failures


def judge_dr8(steps: dict) -> list:
    failures = []
    c1, r1, c2, r2, fin = steps["crash1"], steps["resume1"], steps["crash2"], steps["resume2"], steps["final"]
    if c1["exit_code"] != 137 or c1["accounts_g1_in_product"] != 1 or [o[0] for o in c1["in_flight"]] != [1]:
        failures.append("crash 1 nao deixou a op 1 em voo com a conta criada no produto")
    if not any(o[0] == 1 and o[2] == "sent" for o in steps["restart1"]["ops_after_restart"]):
        failures.append("o estado nao sobreviveu ao restart do container")
    if r1["exit_code"] != 0 or (r1["accounts_g1"], r1["accounts_g2"]) != (1, 1):
        failures.append("retomada 1 duplicou ou perdeu conta")
    if c2["exit_code"] != 137 or c2["approval_status_in_product"] != "approved" or c2["decisions_before_resume"] != 1:
        failures.append("crash 2 nao ocorreu depois da decisao no produto")
    if r2["exit_code"] != 0 or r2["decisions_after_resume"] != 1:
        failures.append("retomada 2 reenviou a decisao")
    if (fin["account_g1_status"], fin["events_pago"], fin["consumptions"], fin["decisions"]) != ("pago", 1, 1, 1):
        failures.append(f"efeito final: {fin}")
    if (fin["accounts_g1"], fin["accounts_g2"]) != (1, 1) or fin["status"]["in_flight"] != []:
        failures.append("contas duplicadas ou ops em voo ao final")
    if set(fin["status"]["counts"]) - {"done", "reconciled"}:
        failures.append(f"ops nao terminais: {fin['status']['counts']}")
    if steps.get("reconciled_seqs") != [1, 4]:
        failures.append(f"reconciliadas {steps.get('reconciled_seqs')} != [1, 4]")
    return failures


# ------------------------------------------------------------------------------------ teardown
def classify_untracked(paths: list, historical: list) -> list:
    """Nao rastreados que NAO sao os historicos autorizados. `historical` termina em `/` para pastas.
    (PRE-LIVE: `--untracked-files=all` expande as pastas; comparar por igualdade exata deu falso FAIL.)"""
    def known(path: str) -> bool:
        return any(path == h or (h.endswith("/") and path.startswith(h)) for h in historical)

    return sorted(p for p in paths if not known(p))


def judge_teardown(data: dict) -> list:
    failures = []
    for key in ("containers", "volumes", "networks"):
        if data.get(key):
            failures.append(f"residuo {key}: {data[key]}")
    if any(data.get("containers_from_lab_images", {}).values()):
        failures.append("container derivado das imagens do lab")
    if data.get("guardprobe_leftover"):
        failures.append("container de sonda residual")
    missing = [i for i in data.get("expected_images", []) if i not in data.get("images", [])]
    if missing:
        failures.append(f"imagens do lab ausentes: {missing}")
    if data.get("head") != data.get("expected_head"):
        failures.append("HEAD mudou")
    if data.get("staged_count") != data.get("expected_staged_count"):
        failures.append(f"staged {data.get('staged_count')} != {data.get('expected_staged_count')}")
    if data.get("tracked_diff"):
        failures.append(f"alteracao em arquivo rastreado: {data['tracked_diff']}")
    if data.get("bytes_mismatch"):
        failures.append(f"bytes diferentes do aprovado: {data['bytes_mismatch']}")
    if data.get("digest") != data.get("expected_digest"):
        failures.append("digest do candidato mudou")
    if data.get("extra_untracked"):
        failures.append(f"nao rastreados alem dos historicos: {data['extra_untracked']}")
    if data.get("sim14_hash_mismatches"):
        failures.append("arquivos congelados do SIM-1.4 mudaram")
    if data.get("dev_snapshot") != "PASS":
        failures.append("snapshot DEV divergiu")
    if not data.get("secrets_outside_repo"):
        failures.append("segredos dentro do repositorio")
    return failures


# ------------------------------------------------------------------------------------ C0b
def distinguish(results: list) -> dict:
    """Separa o resultado do Evaluator em PRODUCT_FACT / EVALUATOR_DERIVATION / DRIVER_EVIDENCE e FALHA DO
    INSTRUMENTO (HARNESS_ERROR). Ausencia de condicoes de derivacao nunca vira CORRECT (KNOWN_LIMIT)."""
    out: dict = {}
    for item in results:
        bucket = out.setdefault(item["evidence_class"], {})
        bucket[item["class"]] = bucket.get(item["class"], 0) + 1
    instrument = sum(v for bucket in out.values() for k, v in bucket.items() if k == INSTRUMENT_FAILURE)
    return {"by_evidence_class": out, "instrument_failures": instrument}


# ------------------------------------------------------------------------------------ D90 (restart + Collector)
RESTART_BARRIER = "after_restart_barrier"
FROZEN_NETWORKS = ["auneron_sim_driver", "auneron_sim_internal"]
IDENTITY_KEYS = ("build_git_sha", "build_git_dirty", "build_source_digest", "build_source_digest_algorithm",
                 "build_identity_state", "environment", "maintenance_enabled", "evidence_floor", "evidence_floor_state")


def first_time(timeline: list, event: str, arg0=None, arg1=None):
    for entry in timeline:
        if entry["event"] != event:
            continue
        args = entry.get("args", [])
        if (arg0 is None or (args and args[0] == arg0)) and (arg1 is None or (len(args) > 1 and args[1] == arg1)):
            return entry["t"]
    return None


def build_d90_report(*, plan: dict, run: dict, second_trigger: dict, driver_status: dict, day: int, at: str) -> dict:
    """Junta a evidencia do D90 num dict puro (sem Docker) para o juiz."""
    state = run["state"]
    timeline = state["timeline"]
    http = [r for r in run["driver_lines"] if r.get("kind") == "http" and r["op"] == "execute_mark_paid"]
    plan_items = {i["item_id"]: i for i in plan["items"]}
    records = []
    for record in run["collected"]:
        item = plan_items[record["item_id"]]
        records.append({"item_id": record["item_id"], "trigger": record["trigger"]["kind"], "safe_at": item["safe_at"],
                        "day": item.get("day"), "at": item.get("at"), "requires": item["requires"], "t_real": record["t_real"]})
    expected = [i["item_id"] for i in plan["items"] if i.get("channel", "http") == "http" and i["safe_at"] == "after_daily_barrier"]
    return {
        "restart": state["restart"], "plan_safe_at": sorted({i["safe_at"] for i in plan["items"]}),
        "times": {
            "restart_end": first_time(timeline, "restart_backend:end"),
            "barrier_begin": first_time(timeline, "restart_barrier:start"), "barrier_end": first_time(timeline, "restart_barrier:end"),
            "mark_barrier": first_time(timeline, "collector_mark_barrier:start", RESTART_BARRIER),
            "mark_done": first_time(timeline, "driver_mark_harness_done:start", None, "restart_backend")},
        "restart_slot": {"day": day, "at": at}, "records": records, "missing": run["missing"], "problems": run["problems"],
        "retrigger": {"collected": second_trigger.get("collected"), "already_collected": second_trigger.get("already_collected"),
                      "expected": len(expected)},
        "driver": {"in_flight": driver_status.get("in_flight"), "counts": driver_status.get("counts"),
                   "execute_statuses": [r["status"] for r in http]},
        "evaluation": {"summary": run["evaluation"]["summary"], "run_valid": run["evaluation"]["run_valid"],
                       "instrument_failures": run["distinction"]["instrument_failures"]},
    }


def judge_d90(d: dict) -> list:
    failures = []
    if RESTART_BARRIER in d["plan_safe_at"]:
        failures.append("o plano contem safe_at=after_restart_barrier (coleta impossivel de cumprir)")
    r = d["restart"]
    if not r.get("completed"):
        failures.append("restart nao concluido")
    if not r.get("barrier_done"):
        failures.append("barreira extraordinaria nao concluida")
    if r.get("docker_restart_code") != 0:
        failures.append("docker restart nao retornou 0")
    before, after = r.get("before", {}), r.get("after", {})
    if not before or not after or after.get("started_at", "") <= before.get("started_at", "~"):
        failures.append("o backend nao reiniciou (StartedAt nao avancou)")
    if not r.get("ready", {}).get("ready"):
        failures.append("o backend nao voltou ready")
    if before.get("networks") != FROZEN_NETWORKS or after.get("networks") != FROZEN_NETWORKS:
        failures.append(f"redes antes/depois: {before.get('networks')} / {after.get('networks')}")
    ib, ia = r.get("identity_before") or {}, r.get("identity_after") or {}
    if not ib or not ia or [k for k in IDENTITY_KEYS if ib.get(k) != ia.get(k)] or ia.get("build_identity_state") != "valid":
        failures.append("identidade/ambiente do build mudou ou invalida")
    control = r.get("pre_restart_control") or {}
    if not (control.get("exit_code") == 3 and "exige" in json_text(control.get("payload"))
            and control.get("collected_before") == control.get("collected_after")):
        failures.append("coleta antecipada (antes da barreira) nao foi recusada")
    t, start = d["times"], r.get("since")
    ordered = [start, t["restart_end"], t["barrier_begin"], t["barrier_end"], t["mark_barrier"], t["mark_done"]]
    if any(x is None for x in ordered) or ordered != sorted(ordered):
        failures.append(f"ordem restart -> barreira -> marca da barreira -> harness-done violada: {ordered}")
    barrier_end = t["barrier_end"] or float("inf")
    slot = d["restart_slot"]
    needing = [x for x in d["records"] if RESTART_BARRIER in x["requires"]]
    if not needing or any(x["t_real"] <= barrier_end or x["trigger"] != "after_daily_barrier" for x in needing):
        failures.append("itens com requires=after_restart_barrier ausentes ou coletados antes da barreira")
    at_slot = [x for x in d["records"] if x["day"] == slot["day"] and x["at"] == slot["at"]]
    pre = [x for x in at_slot if x["safe_at"] == "pre_slot"]
    post = [x for x in at_slot if x["safe_at"] == "post_slot"]
    if not pre or any(x["t_real"] >= (start or 0) for x in pre):
        failures.append("pre_slot do slot do restart ausente ou coletado depois do restart")
    if not post or any(x["t_real"] <= barrier_end for x in post):
        failures.append("post_slot do slot do restart ausente ou coletado antes da barreira")
    ids = [x["item_id"] for x in d["records"]]
    if d["missing"] or d["problems"] or len(ids) != len(set(ids)):
        failures.append(f"coleta faltante/duplicada/fora de ponto: missing={d['missing']} problems={d['problems']}")
    rt = d["retrigger"]
    if not (rt["collected"] == 0 and rt["already_collected"] == rt["expected"] > 0):
        failures.append(f"re-disparo nao e idempotente: {rt}")
    drv = d["driver"]
    if drv["in_flight"] != [] or set(drv["counts"] or {}) - {"done", "reconciled"} or not drv["counts"]:
        failures.append("Driver nao continuou/terminou limpo apos o restart")
    if not drv["execute_statuses"] or drv["execute_statuses"][-1] != 200:
        failures.append("execucao da aprovacao pre-restart nao teve sucesso apos o restart")
    ev = d["evaluation"]
    if set(ev["summary"]) != {"CORRECT"} or not ev["run_valid"] or ev["instrument_failures"]:
        failures.append(f"avaliacao: {ev}")
    return failures


def json_text(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
