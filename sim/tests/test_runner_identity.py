"""
SIM-1.5E -- identidade e controle: RIM / `run_id`, registro do host, ciclo de vida sem retrocesso, ledger com hash
encadeado (alteracao, reordenacao, truncamento), INTERLOCK-D0 e taxonomia terminal.
"""

from __future__ import annotations

import json
import os

import pytest

from sim.runner import interlock
from sim.runner import lifecycle
from sim.runner import rim as rim_mod
from sim.runner import taxonomy
from sim.runner.errors import InterlockError
from sim.runner.errors import LedgerError
from sim.runner.errors import LifecycleError
from sim.runner.errors import RegistryError
from sim.runner.errors import RimError
from sim.runner.errors import RunnerError
from sim.runner.ledger import ANCHOR_NAME
from sim.runner.ledger import LEDGER_NAME
from sim.runner.ledger import RunLedger
from sim.runner.registry import Registry
from sim.tests.runner_support import declared
from sim.tests.runner_support import make_rim
from sim.tests.runner_support import mutated
from sim.tests.runner_support import observed
from sim.tests.runner_support import sha


# ------------------------------------------------------------------------------------------------------------- RIM
def test_rim_is_deterministic_and_run_id_has_the_frozen_format():
    rim = make_rim()
    shuffled = json.loads(json.dumps(rim, sort_keys=False))
    assert rim_mod.rim_sha256(rim) == rim_mod.rim_sha256(shuffled)
    sha_full = rim_mod.rim_sha256(rim)
    assert rim_mod.rim12(rim) == sha_full[:12]
    run_id = rim_mod.make_run_id(sha_full, 1)
    assert run_id == f"RUN-{sha_full[:12]}-A01" and rim_mod.parse_run_id(run_id) == (sha_full[:12], 1)
    for bad in ("RUN-abc-A01", f"RUN-{sha_full[:12]}-A00", f"run-{sha_full[:12]}-A01", f"RUN-{sha_full[:12]}-A1", ""):
        with pytest.raises(RimError):
            rim_mod.parse_run_id(bad)
    with pytest.raises(RimError):
        rim_mod.make_run_id(sha_full, 100)


@pytest.mark.parametrize("mutation", [
    lambda r: r["declared"].update(extra_key="x"),                      # esquema fechado: chave a mais
    lambda r: r["declared"].pop("plan_sha256"),                          # chave a menos
    lambda r: r["declared"].update(seed=1.5),                            # float proibido
    lambda r: r["declared"].update(oracle_sha256="xyz"),                 # nao e sha256
    lambda r: r["declared"].update(repo_commit=""),
    lambda r: r["declared"].update(recovery_policy="R1"),                # 1o run: R0
    lambda r: r["declared"]["timeline"].update(restart_day=999),
    lambda r: r["declared"]["disk_policy"].update(abort_bytes=10 ** 12),
    lambda r: r["observed"].update(started_at="2026-01-01T00:00:00Z"),   # horario nao entra no RIM
    lambda r: r["observed"].update(instance_id="sim15a-x"),
    lambda r: r.update(hostname="maquina"),
])
def test_rim_schema_is_closed_and_refuses_what_must_not_define_identity(mutation):
    rim = make_rim()
    mutation(rim)
    with pytest.raises(RimError):
        rim_mod.validate_rim(rim)


def test_a_run_requires_a_measured_growth_model_and_a_rehearsal_does_not():
    d = declared()
    with pytest.raises(RimError, match="modelo de crescimento"):
        rim_mod.build_rim("RUN", d, observed(d))
    assert rim_mod.build_rim("REHEARSAL", d, observed(d))["kind"] == "REHEARSAL"
    d_growth = declared(growth=sha("9"))
    assert rim_mod.build_rim("RUN", d_growth, observed(d_growth))["kind"] == "RUN"


def test_any_change_of_what_is_executed_changes_the_rim_and_therefore_the_run_id():
    base = make_rim()
    for path, value in (("declared.oracle_sha256", sha("0")), ("declared.repo_commit", "d" * 40),
                        ("declared.trees.sim_runner", "other"), ("declared.timeline.last_day", 4),
                        ("declared.base_image_digest", "python@sha256:" + "0" * 64)):
        assert rim_mod.rim12(mutated(base, path, value)) != rim_mod.rim12(base), path


def test_observed_facts_that_contradict_the_declared_are_reported():
    rim = make_rim()
    assert rim_mod.observed_mismatches(rim) == []
    for path in ("observed.driver_manifest_agenda_sha256", "observed.driver_image.base_digest", "observed.collector_image.tree"):
        problems = rim_mod.observed_mismatches(mutated(rim, path, sha("0") if "sha256" in path else "x"))
        assert problems, path


# ------------------------------------------------------------------------------------------------------------- registro
def finish(registry, info, state="ABORTED", cls="OPERATOR_STOP"):
    registry.record_terminal(info["run_id"], state, cls, {"seq": 1, "hash": "0" * 64})


def test_registry_allocates_monotonic_attempts_and_never_reuses_a_number(tmp_path):
    registry, rim = Registry(tmp_path), make_rim()
    first = registry.open_run(rim, purpose="FIRST")
    assert first["attempt"] == 1 and first["run_id"].endswith("-A01") and first["run_dir"].is_dir()
    with pytest.raises(RegistryError, match="um run por vez"):
        registry.open_run(rim, purpose="RETRY", parent_run_id=first["run_id"], reason="x")
    finish(registry, first)
    second = registry.open_run(rim, purpose="RETRY", parent_run_id=first["run_id"], reason="aborto do 1o")
    assert second["attempt"] == 2 and second["run_id"] != first["run_id"]
    finish(registry, second, "INVALID", "HARNESS_ERROR")
    third = registry.open_run(rim, purpose="RETRY", parent_run_id=second["run_id"], reason="invalid")
    assert [first["attempt"], second["attempt"], third["attempt"]] == [1, 2, 3]
    assert len({first["run_id"], second["run_id"], third["run_id"]}) == 3


def test_a_different_rim_has_a_different_family_and_restarts_at_attempt_one(tmp_path):
    registry = Registry(tmp_path)
    a = registry.open_run(make_rim(), purpose="FIRST")
    finish(registry, a)
    b = registry.open_run(make_rim(last_day=9), purpose="FIRST")
    assert b["attempt"] == 1 and b["rim12"] != a["rim12"]


def test_purpose_rules_retry_reproduction_and_first(tmp_path):
    registry, rim = Registry(tmp_path), make_rim()
    first = registry.open_run(rim, purpose="FIRST")
    finish(registry, first)
    with pytest.raises(RegistryError, match="declare RETRY ou REPRODUCTION"):
        registry.open_run(rim, purpose="FIRST")
    with pytest.raises(RegistryError, match="parent_run_id"):
        registry.open_run(rim, purpose="RETRY", reason="x")
    with pytest.raises(RegistryError, match="RETRY exige"):
        registry.open_run(rim, purpose="RETRY", parent_run_id=first["run_id"])             # sem razao
    with pytest.raises(RegistryError, match="REPRODUCTION exige um run COMPLETED"):
        registry.open_run(rim, purpose="REPRODUCTION", parent_run_id=first["run_id"])
    with pytest.raises(RegistryError, match="primeira tentativa"):
        Registry(tmp_path / "other").open_run(rim, purpose="RETRY", parent_run_id="RUN-x-A01", reason="x")


def test_retry_after_a_completed_run_is_refused_but_reproduction_is_allowed(tmp_path):
    registry, rim = Registry(tmp_path), make_rim()
    first = registry.open_run(rim, purpose="FIRST")
    registry.record_terminal(first["run_id"], "COMPLETED", None, {"seq": 1, "hash": "0" * 64}, "d" * 64)
    with pytest.raises(RegistryError, match="RETRY exige parent ABORTED/INVALID"):
        registry.open_run(rim, purpose="RETRY", parent_run_id=first["run_id"], reason="x")
    repro = registry.open_run(rim, purpose="REPRODUCTION", parent_run_id=first["run_id"])
    assert repro["attempt"] == 2


def test_existing_run_directory_is_never_reused(tmp_path):
    registry, rim = Registry(tmp_path), make_rim()
    sha_full = rim_mod.rim_sha256(rim)
    (tmp_path / rim_mod.make_run_id(sha_full, 1)).mkdir()                       # diretorio orfao
    with pytest.raises(RegistryError, match="nunca e reutilizado"):
        registry.open_run(rim, purpose="FIRST")
    assert not registry.path.exists() or registry.events() == []


def test_rim_prefix_collision_is_a_stop(tmp_path):
    registry, rim = Registry(tmp_path), make_rim()
    sha_full = rim_mod.rim_sha256(rim)
    registry._append("OPENED", {"run_id": "RUN-" + sha_full[:12] + "-A01", "rim_sha256": sha_full[:12] + "f" * 52,
                                "rim12": sha_full[:12], "attempt": 1, "kind": "REHEARSAL", "purpose": "FIRST",
                                "parent_run_id": None, "reason": None})
    registry.record_terminal("RUN-" + sha_full[:12] + "-A01", "ABORTED", "OPERATOR_STOP", {"seq": 1, "hash": "0" * 64})
    with pytest.raises(RegistryError, match="RIM_PREFIX_COLLISION"):
        registry.open_run(rim, purpose="FIRST")


def test_registry_detects_tampering_and_concurrent_allocators(tmp_path):
    registry, rim = Registry(tmp_path), make_rim()
    info = registry.open_run(rim, purpose="FIRST")
    finish(registry, info)
    lines = registry.path.read_bytes().split(b"\n")
    registry.path.write_bytes(lines[0].replace(b"REHEARSAL", b"RUN") + b"\n" + lines[1] + b"\n")
    with pytest.raises(RegistryError, match="corrompido"):
        registry.events()
    clean = Registry(tmp_path / "clean")
    clean.lock_path.write_bytes(b"")
    with pytest.raises(RegistryError, match="outro alocador"):
        clean.open_run(rim, purpose="FIRST")


def test_terminal_state_is_recorded_once(tmp_path):
    registry = Registry(tmp_path)
    info = registry.open_run(make_rim(), purpose="FIRST")
    finish(registry, info)
    with pytest.raises(RegistryError, match="terminal impossivel"):
        finish(registry, info, "INVALID", "HARNESS_ERROR")
    assert registry.state_of(info["run_id"]) == "ABORTED"


# ------------------------------------------------------------------------------------------------------------- lifecycle
OPEN = ("OPENED", {"timeline": {"last_day": 2}})


def replay(*events):
    return lifecycle.replay([OPEN, *events])


HAPPY = [("RIM_VERIFIED", {}), ("PREFLIGHT_PASSED", {}), ("D0_AUTHORIZED", {"token_sha256": "t"}), ("RUN_STARTED", {}),
         ("DAY_STARTED", {"day": 0}), ("DAY_CLOSED", {"day": 0}), ("DAY_STARTED", {"day": 1}), ("DAY_CLOSED", {"day": 1}),
         ("DAY_STARTED", {"day": 2}), ("DAY_CLOSED", {"day": 2}), ("RUN_FINALIZING", {}), ("PACKAGE_SEALED", {"digest": "d"}),
         ("COMPLETED", {})]


def test_lifecycle_happy_path_and_post_run_annotations():
    state = replay(*HAPPY)
    assert state.state == "COMPLETED" and state.last_closed == 2
    state.apply("EVALUATED", {})
    state.apply("DESIGNATED_CANONICAL", {})
    assert state.state == "COMPLETED"                                        # anotacoes nao mudam o estado


@pytest.mark.parametrize("name,events,message", [
    ("run started sem interlock", [("PREFLIGHT_PASSED", {}), ("RUN_STARTED", {})], "sem D0_AUTHORIZED"),
    ("autorizacao antes do preflight", [("D0_AUTHORIZED", {"token_sha256": "t"})], "proibido em CREATED"),
    ("dia antes de RUNNING", [("PREFLIGHT_PASSED", {}), ("DAY_STARTED", {"day": 0})], "proibido em PREFLIGHTED"),
    ("autorizacao de uso unico", HAPPY[:3] + [("D0_AUTHORIZED", {"token_sha256": "t"})], "uso unico"),
    ("run started duas vezes", HAPPY[:4] + [("RUN_STARTED", {})], "proibido em RUNNING"),
    ("pular o dia 0", HAPPY[:4] + [("DAY_STARTED", {"day": 1})], "fora de ordem"),
    ("repetir o dia 0", HAPPY[:6] + [("DAY_STARTED", {"day": 0})], "fora de ordem"),
    ("dia aberto sobreposto", HAPPY[:5] + [("DAY_STARTED", {"day": 1})], "ainda aberto"),
    ("fechar dia nao aberto", HAPPY[:4] + [("DAY_CLOSED", {"day": 0})], "sem DAY_STARTED"),
    ("dia alem do ultimo", HAPPY[:10] + [("DAY_STARTED", {"day": 3})], "alem de last_day"),
    ("finalizar com dias faltando", HAPPY[:6] + [("RUN_FINALIZING", {})], "todos os dias"),
    ("completed sem pacote", HAPPY[:11] + [("COMPLETED", {})], "COMPLETED exige"),
    ("selo sem finalizar", HAPPY[:10] + [("PACKAGE_SEALED", {"digest": "d"})], "antes de RUN_FINALIZING"),
    ("evento desconhecido", [("INVENTADO", {})], "evento desconhecido"),
    ("OPENED duas vezes", [("OPENED", {"timeline": {"last_day": 2}})], "primeiro evento"),
])
def test_lifecycle_refuses_every_illegal_sequence(name, events, message):
    with pytest.raises(LifecycleError, match=message):
        replay(*events)


@pytest.mark.parametrize("terminal", ["ABORTED", "INVALID"])
def test_terminal_states_are_absorbing_nothing_returns_to_running(terminal):
    state = replay(*HAPPY[:4], (terminal, {"class": "x"}))
    assert state.state == terminal
    for event, payload in (("RUN_STARTED", {}), ("DAY_STARTED", {"day": 0}), ("PREFLIGHT_PASSED", {}),
                           ("COMPLETED", {}), ("ABORTED", {}), ("INVALID", {}), ("EVALUATED", {}), ("D0_AUTHORIZED", {"token_sha256": "t"})):
        with pytest.raises(LifecycleError):
            state.apply(event, payload)


def test_completed_is_terminal_and_only_post_run_annotations_follow():
    state = replay(*HAPPY)
    for event in ("RUN_STARTED", "DAY_STARTED", "ABORTED", "INVALID", "COMPLETED"):
        with pytest.raises(LifecycleError):
            state.apply(event, {"day": 0})
    state.apply("INVALIDATED_POST_HOC", {})
    with pytest.raises(LifecycleError):
        state.apply("DESIGNATED_CANONICAL", {})                              # run invalidado nao e canonico


def test_aborted_run_accepts_only_the_partial_package_annotation():
    state = replay(*HAPPY[:4], ("ABORTED", {"class": "OPERATOR_STOP"}))
    state.apply("PARTIAL_PACKAGE_SEALED", {})
    with pytest.raises(LifecycleError):
        state.apply("EVALUATED", {})


# ------------------------------------------------------------------------------------------------------------- ledger
def opened_ledger(tmp_path, last_day=2):
    ledger = RunLedger(tmp_path / "run")
    ledger.open({"run_id": "RUN-x", "rim_sha256": "s", "kind": "REHEARSAL", "timeline": {"last_day": last_day}})
    return ledger


def advance_to_running(ledger):
    ledger.append("PREFLIGHT_PASSED", {})
    ledger.authorize_d0("tok")
    ledger.append("RUN_STARTED", {})


def test_ledger_persists_chain_state_and_resumes_the_state_machine_on_reload(tmp_path):
    ledger = opened_ledger(tmp_path)
    advance_to_running(ledger)
    ledger.append("DAY_STARTED", {"day": 0})
    again = RunLedger(tmp_path / "run")
    assert again.state == "RUNNING" and again.replay.open_day == 0 and again.tail == ledger.tail
    with pytest.raises(LifecycleError):
        again.append("DAY_STARTED", {"day": 1})
    assert again.verify()["events"] == len(ledger.events())


def test_an_illegal_event_writes_nothing(tmp_path):
    ledger = opened_ledger(tmp_path)
    before = (ledger.path.read_bytes(), ledger.anchor_path.read_bytes())
    with pytest.raises(LifecycleError):
        ledger.append("RUN_STARTED", {})
    assert (ledger.path.read_bytes(), ledger.anchor_path.read_bytes()) == before


def test_reserved_events_cannot_be_appended_generically(tmp_path):
    ledger = opened_ledger(tmp_path)
    ledger.append("PREFLIGHT_PASSED", {})
    for event in ("D0_AUTHORIZED", "OPENED"):
        with pytest.raises(LedgerError, match="reservado"):
            ledger.append(event, {"token_sha256": "t"})


def edit_line(path, number, fn):
    lines = path.read_bytes().split(b"\n")
    lines[number] = fn(lines[number])
    path.write_bytes(b"\n".join(lines))


def test_ledger_detects_alteration_reordering_removal_and_truncation(tmp_path):
    ledger = opened_ledger(tmp_path)
    advance_to_running(ledger)
    ledger.append("DAY_STARTED", {"day": 0})
    ledger.append("DAY_CLOSED", {"day": 0})
    assert ledger.verify()["warning"] is None
    original = ledger.path.read_bytes()

    edit_line(ledger.path, 3, lambda raw: raw.replace(b"RUN_STARTED", b"INVALID_"))                       # alteracao
    with pytest.raises(LedgerError, match="hash invalido"):
        RunLedger(tmp_path / "run")
    lines = original.split(b"\n")
    ledger.path.write_bytes(b"\n".join([lines[0], lines[2], lines[1], *lines[3:]]))                    # reordenacao
    with pytest.raises(LedgerError):
        RunLedger(tmp_path / "run")
    ledger.path.write_bytes(b"\n".join([lines[0], lines[1], *lines[3:]]))                              # remocao no meio
    with pytest.raises(LedgerError):
        RunLedger(tmp_path / "run")
    ledger.path.write_bytes(b"\n".join(lines[:-2]) + b"\n")                                            # truncamento do fim
    with pytest.raises(LedgerError, match="truncamento"):
        RunLedger(tmp_path / "run").verify()
    ledger.path.write_bytes(original)
    ledger.anchor_path.unlink()
    with pytest.raises(LedgerError, match="sem ancora"):
        RunLedger(tmp_path / "run").verify()


def test_ledger_rewritten_consistently_but_rolled_back_is_still_caught_by_the_anchor_or_package(tmp_path):
    ledger = opened_ledger(tmp_path)
    advance_to_running(ledger)
    ledger.append("DAY_STARTED", {"day": 0})
    anchor_before = ledger.anchor_path.read_bytes()
    ledger.append("DAY_CLOSED", {"day": 0})
    # ledger devolvido a um prefixo VALIDO (cadeia integra), mas a ancora conta a verdade
    lines = ledger.path.read_bytes().split(b"\n")
    ledger.path.write_bytes(b"\n".join(lines[:-2]) + b"\n")
    assert RunLedger(tmp_path / "run").state == "RUNNING"                    # a cadeia sozinha nao basta...
    with pytest.raises(LedgerError, match="truncamento"):
        RunLedger(tmp_path / "run").verify()                                  # ...a ancora pega
    assert anchor_before != ledger.anchor_path.read_bytes()


def test_a_crash_between_append_and_anchor_is_tolerated_only_by_one_event(tmp_path):
    ledger = opened_ledger(tmp_path)
    ledger.append("PREFLIGHT_PASSED", {})
    stale = ledger.anchor_path.read_bytes()
    ledger.authorize_d0("tok")
    ledger.anchor_path.write_bytes(stale)
    assert RunLedger(tmp_path / "run").verify()["warning"] == "anchor_lag"
    ledger.append("RUN_STARTED", {})
    ledger.anchor_path.write_bytes(stale)
    with pytest.raises(LedgerError, match="mais de um evento"):
        RunLedger(tmp_path / "run").verify()


def test_ledger_terminate_follows_the_taxonomy_and_refuses_unknown_classes(tmp_path):
    ledger = opened_ledger(tmp_path)
    with pytest.raises(RunnerError, match="desconhecida"):
        ledger.terminate("QUALQUER_COISA")
    ledger.terminate("PREFLIGHT_FAILED")
    assert ledger.state == "ABORTED"
    ledger2 = RunLedger(tmp_path / "other")
    ledger2.open({"run_id": "r", "rim_sha256": "s", "kind": "RUN", "timeline": {"last_day": 1}})
    ledger2.terminate("HARNESS_ERROR")
    assert ledger2.state == "INVALID"


def test_taxonomy_maps_every_class_to_exactly_one_terminal_state():
    assert taxonomy.INVALID_CLASSES.isdisjoint(taxonomy.ABORTED_CLASSES)
    assert taxonomy.terminal_state("CLOCK_VIOLATION") == "INVALID" and taxonomy.terminal_state("DISK_LOW") == "ABORTED"
    assert taxonomy.worst_state(["DISK_LOW", "RESTART_FAILURE"]) == "INVALID"
    assert taxonomy.worst_state(["DISK_LOW", "OPERATOR_STOP"]) == "ABORTED"
    for result_class in taxonomy.NON_TERMINAL_RESULT_CLASSES:                 # resultado de negocio nunca vira terminal
        with pytest.raises(RunnerError):
            taxonomy.terminal_state(result_class)


# ------------------------------------------------------------------------------------------------------------- interlock
def preflighted(tmp_path, kind="REHEARSAL"):
    ledger = RunLedger(tmp_path / "run")
    ledger.open({"run_id": "RUN-x-A01", "rim_sha256": "s" * 64, "kind": kind, "timeline": {"last_day": 2}})
    ledger.append("PREFLIGHT_PASSED", {})
    return ledger


def test_enter_running_without_a_token_changes_nothing(tmp_path):
    ledger = preflighted(tmp_path)
    before = ledger.path.read_bytes()
    with pytest.raises(InterlockError, match="ausente"):
        interlock.enter_running(ledger, ledger.dir, run_id="RUN-x-A01", rim_sha256="s" * 64, kind="REHEARSAL")
    assert ledger.state == "PREFLIGHTED" and ledger.path.read_bytes() == before


def test_enter_running_with_a_valid_token_is_the_only_path_and_it_is_single_use(tmp_path):
    ledger = preflighted(tmp_path)
    token = interlock.authorize_rehearsal(ledger.dir, "RUN-x-A01", "s" * 64, "REHEARSAL", "PO", "ref")
    interlock.enter_running(ledger, ledger.dir, run_id="RUN-x-A01", rim_sha256="s" * 64, kind="REHEARSAL")
    assert ledger.state == "RUNNING"
    assert [r["event"] for r in ledger.events()][-2:] == ["D0_AUTHORIZED", "RUN_STARTED"]
    assert ledger.events()[-2]["payload"]["token_sha256"] == token["token_sha256"]
    with pytest.raises(InterlockError, match="so PREFLIGHTED"):
        interlock.enter_running(ledger, ledger.dir, run_id="RUN-x-A01", rim_sha256="s" * 64, kind="REHEARSAL")
    interlock.verify_ledger_authorization(ledger, ledger.dir)


@pytest.mark.parametrize("change", [
    {"run_id": "RUN-y-A01"}, {"rim_sha256": "z" * 64}, {"kind": "RUN"}, {"authorized_by": " "}, {"reference": ""},
    {"token_sha256": "0" * 64}, {"extra": 1}, {"schema": "outro"},
])
def test_tampered_or_foreign_tokens_never_authorize(tmp_path, change):
    ledger = preflighted(tmp_path)
    token = interlock.build_token("RUN-x-A01", "s" * 64, "REHEARSAL", "PO", "ref")
    token.update(change)
    (ledger.dir / interlock.TOKEN_NAME).write_text(json.dumps(token), encoding="utf-8")
    with pytest.raises(InterlockError):
        interlock.enter_running(ledger, ledger.dir, run_id="RUN-x-A01", rim_sha256="s" * 64, kind="REHEARSAL")
    assert ledger.state == "PREFLIGHTED"


def test_this_package_never_issues_a_d0_authorization_for_a_real_run(tmp_path):
    ledger = preflighted(tmp_path, kind="RUN")
    with pytest.raises(InterlockError, match="exclusiva do PO"):
        interlock.authorize_rehearsal(ledger.dir, "RUN-x-A01", "s" * 64, "RUN", "PO", "ref")
    assert not (ledger.dir / interlock.TOKEN_NAME).exists()
    with pytest.raises(InterlockError):
        interlock.enter_running(ledger, ledger.dir, run_id="RUN-x-A01", rim_sha256="s" * 64, kind="RUN")
    assert ledger.state == "PREFLIGHTED"


def test_forging_the_ledger_events_directly_is_caught_by_the_post_hoc_authorization_check(tmp_path):
    ledger = preflighted(tmp_path)
    ledger.authorize_d0("forjado")                                           # contorna o interlock chamando o metodo do ledger
    ledger.append("RUN_STARTED", {})
    with pytest.raises(InterlockError, match="ausente"):
        interlock.verify_ledger_authorization(ledger, ledger.dir)
    token = interlock.build_token("RUN-x-A01", "s" * 64, "REHEARSAL", "PO", "ref")
    (ledger.dir / interlock.TOKEN_NAME).write_text(json.dumps(token), encoding="utf-8")
    with pytest.raises(InterlockError, match="difere do registrado"):
        interlock.verify_ledger_authorization(ledger, ledger.dir)


def test_ledger_files_are_plain_and_relative_names_are_stable():
    assert LEDGER_NAME == "ledger.jsonl" and ANCHOR_NAME == "ledger.anchor" and os.sep


# ------------------------------------------------------------------------------------------------------------- defesa em profundidade
def forge_second_line(tmp_path, **fields):
    """Reescreve a linha 2 do ledger com os campos dados e hash RECALCULADO (so a checagem especifica pode pegar)."""
    from sim.runner.ledger import line_hash

    ledger = opened_ledger(tmp_path)
    ledger.append("PREFLIGHT_PASSED", {})
    lines = ledger.path.read_bytes().split(b"\n")
    record = json.loads(lines[1])
    record.update(fields)
    record["hash"] = line_hash(record)
    from sim.generator.canonical import canonical_bytes

    ledger.path.write_bytes(lines[0] + b"\n" + canonical_bytes(record))


def test_ledger_seq_is_checked_independently_of_the_chain_and_the_hash(tmp_path):
    forge_second_line(tmp_path, seq=5)
    with pytest.raises(LedgerError, match="seq"):
        RunLedger(tmp_path / "run")


def test_ledger_chain_link_is_checked_independently_of_seq_and_hash(tmp_path):
    forge_second_line(tmp_path, prev="f" * 64)
    with pytest.raises(LedgerError, match="encadeamento"):
        RunLedger(tmp_path / "run")


def test_a_correctly_signed_token_for_another_run_rim_or_kind_is_still_refused(tmp_path):
    ledger = preflighted(tmp_path)
    for run_id, rim_sha, kind in (("RUN-other-A01", "s" * 64, "REHEARSAL"), ("RUN-x-A01", "o" * 64, "REHEARSAL"),
                                  ("RUN-x-A01", "s" * 64, "RUN")):
        token = interlock.build_token(run_id, rim_sha, kind, "PO", "ref")             # token VALIDO (hash correto), mas de outro contexto
        (ledger.dir / interlock.TOKEN_NAME).write_text(json.dumps(token), encoding="utf-8")
        with pytest.raises(InterlockError, match="outro run"):
            interlock.enter_running(ledger, ledger.dir, run_id="RUN-x-A01", rim_sha256="s" * 64, kind="REHEARSAL")
    assert ledger.state == "PREFLIGHTED"


def test_terminal_states_are_refused_by_the_absorbing_rule_not_only_by_the_per_event_table():
    state = replay(*HAPPY[:4], ("ABORTED", {"class": "x"}))
    with pytest.raises(LifecycleError, match="nada sai de um estado terminal"):
        state.apply("RUN_STARTED", {})
