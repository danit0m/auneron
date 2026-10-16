"""
CLI do plano de controle do run:

    # OFFLINE (sem Docker, so arquivos)
    python -m sim.runner.cli verify-ledger   --run-id RUN-...
    python -m sim.runner.cli verify-package  --run-id RUN-... [--digest <sha>]
    python -m sim.runner.cli seal-unclean    --run-id RUN-...
    python -m sim.runner.cli authorize-rehearsal --run-id RUN-... --by <quem> --reference <texto>   # NUNCA para kind=RUN
    python -m sim.runner.cli claim           --run-id RUN-...
    python -m sim.runner.cli evaluate        --run-id RUN-... [--verify-only]
    python -m sim.runner.cli verify-results  --run-id RUN-...
    python -m sim.runner.cli growth-model    --run-id RUN-... [--out <arquivo>]
    python -m sim.runner.cli contamination-scan --root <dir> [--allow <caminho relativo> ...]

    # PROCESSO DESTACADO (padrao: Windows Task Scheduler, SIM-1.5G; WMI foi reprovado no DETACH-1 e esta desabilitado)
    python -m sim.runner.cli launch|status|stop|kill|tail --run-id RUN-...
    python -m sim.runner.cli recover --run-id RUN-... [--confirm] [--grace-s N]   # SIM-1.5G.4: processos do run por identidade comprovada
    python -m sim.runner.cli sched-list
    python -m sim.runner.cli sched-audit-verify|sched-reconcile --run-id RUN-... [--apply [--confirm]]
    python -m sim.runner.cli sched-probe --phase launch|monitor|finish --level L1|L2 [--name <sonda>]   # DETACH-2 (autorizacao do PO)
        # SIM-1.5G.3: o L2 exige descobrir o CLI da sessao iniciadora (ambiguo => nada e registrado); --watch-pid vigia um PID
        # auxiliar (controles); --session-end-epoch so vale para evidencia historica (na v2 o fim vem da vigilia duravel)

    # LIVE (precisam de um lab ja de pe; NAO exercitados nesta etapa)
    python -m sim.runner.cli stage|open|preflight|run --instance <id> [--bundle nh|sr1]

`run` so entra em RUNNING com D0_AUTHORIZATION.json valido. Este CLI nao tem comando que emita essa autorizacao para
`kind = RUN`. Codigos de saida: 0 ok; 1 reprovado; 3 violacao de contrato (R0, interlock, ledger).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sim.runner import claim as claim_mod
from sim.runner import interlock
from sim.runner import package
from sim.runner import results
from sim.runner import runner as runner_mod
from sim.runner.errors import RunnerError
from sim.runner.ledger import RunLedger
from sim.runner.registry import Registry

OFFLINE = ("verify-ledger", "verify-package", "verify-results", "seal-unclean", "authorize-rehearsal", "claim", "evaluate",
           "growth-model", "contamination-scan")
PROCESS = ("launch", "status", "stop", "kill", "tail", "recover")
SCHED = ("sched-list", "sched-audit-verify", "sched-reconcile", "sched-probe")
LIVE = ("stage", "open", "preflight", "run")
NEEDS_RUN_ID = OFFLINE[:-1] + PROCESS + ("preflight", "run", "sched-audit-verify", "sched-reconcile")
PROFILE_KEYS = {"ops": "ops_total", "accounts": "accounts_total", "days": "days_total"}


def _runs_root(args) -> Path:
    if args.runs_root:
        return Path(args.runs_root)
    from sim.runner import nh_phases

    return nh_phases.runs_root()


def _print(doc) -> None:
    print(json.dumps(doc, sort_keys=True, default=str))


def _real_hours(ledger: RunLedger) -> float:
    import calendar
    import time

    def epoch(event: str) -> int:
        record = next(r for r in ledger.events() if r["event"] == event)
        return calendar.timegm(time.strptime(record["t_real"], "%Y-%m-%dT%H:%M:%SZ"))

    return max(epoch("COMPLETED") - epoch("RUN_STARTED"), 1) / 3600


def _growth_model(args, root: Path, run_dir: Path, registry: Registry) -> int:
    from sim.rehearsal import profile as rehearsal_profile
    from sim.runner import growth

    ledger = RunLedger(run_dir)
    ledger.verify()
    if ledger.state != "COMPLETED":
        raise RunnerError("o modelo de crescimento so sai de um ensaio COMPLETED")
    rim = json.loads((run_dir / "rim.json").read_bytes().decode("utf-8"))
    if rim["kind"] != "REHEARSAL":
        raise RunnerError("o modelo de crescimento vem de um REHEARSAL")
    profile = rehearsal_profile.load_profile()
    terminal = registry.runs()[args.run_id]["terminal"]
    model = growth.build_growth_model(
        run_dir, nh_targets={k: profile[v] for k, v in PROFILE_KEYS.items()}, real_hours=_real_hours(ledger),
        source_run_id=args.run_id, package_digest=terminal["package_digest"])
    data = growth.model_bytes(model)
    out = Path(args.out) if args.out else run_dir / "growth_model.json"
    out.write_bytes(data)
    registry.record_annotation(args.run_id, "GROWTH_MODEL", {"growth_model_sha256": growth.model_sha256(model)})
    _print({"growth_model_sha256": growth.model_sha256(model), "projected_growth_bytes": model["projected_growth_bytes"],
            "out": str(out)})
    return 0


def _offline(args) -> int:
    from sim.runner import harness_side

    if args.command == "contamination-scan":
        from sim.rehearsal import contamination

        report = contamination.scan_tree(Path(args.root), allow_paths=tuple(args.allow or ()))
        _print({"ok": not report["hits"], **report})
        return 0 if not report["hits"] else 1
    root = _runs_root(args)
    run_dir = root / args.run_id
    registry = Registry(root)
    if args.command == "verify-ledger":
        report = RunLedger(run_dir).verify()
        interlock.verify_ledger_authorization(RunLedger(run_dir), run_dir)
        _print({"ok": True, **report})
        return 0
    if args.command == "verify-package":
        report = package.verify(run_dir, expected_digest=args.digest)
        _print(report)
        return 0 if report["ok"] else 1
    if args.command == "verify-results":
        report = results.verify_results(run_dir)
        _print(report)
        return 0 if report["ok"] else 1
    if args.command == "seal-unclean":
        _print({"sealed": runner_mod.seal_unclean_exit(run_dir, registry)})
        return 0
    if args.command == "authorize-rehearsal":
        rim = json.loads((run_dir / "rim.json").read_bytes().decode("utf-8"))
        token = interlock.authorize_rehearsal(run_dir, args.run_id, json.loads((run_dir / "run_id.json").read_bytes())["rim_sha256"],
                                              rim["kind"], args.by, args.reference)
        _print({"token_sha256": token["token_sha256"]})
        return 0
    if args.command == "evaluate":
        try:
            report = harness_side.evaluate_package(run_dir, registry=registry, verify_only=args.verify_only)
        except harness_side.EvaluateRefused as error:
            _print({"ok": False, "refused": str(error)})
            return 1
        if args.verify_only:
            _print(report)
            return 0 if report["verified"] else 1
        _print({"ok": True, "summary": report["evaluation"]["summary"], "run_valid": report["evaluation"]["run_valid"],
                "surprises_clean": report["surprises"]["clean"], "results_digest": report["results"]["digest"]})
        return 0
    if args.command == "growth-model":
        return _growth_model(args, root, run_dir, registry)
    ledger = RunLedger(run_dir)
    rim = json.loads((run_dir / "rim.json").read_bytes().decode("utf-8"))
    _print(claim_mod.claim_for(kind=rim["kind"], state=ledger.state, run_id=args.run_id,
                               oracle_version=rim["declared"]["oracle_version"], evaluated="EVALUATED" in ledger.replay.seen,
                               invalidated_post_hoc="INVALIDATED_POST_HOC" in ledger.replay.seen))
    return 0


def _process(args) -> int:
    from sim.runner import detach
    from sim.runner import sched

    root = _runs_root(args)
    proc = detach.proc_dir(root, args.run_id)
    run_dir = root / args.run_id
    if args.command == "status":
        state = RunLedger(run_dir).state if run_dir.exists() else None
        launch_doc = detach.read_launch(proc)
        task, task_error = None, None
        if launch_doc and launch_doc.get("mechanism") == sched.MECHANISM:
            try:
                task = sched.info(sched.run_default, launch_doc["run_id"])
            except sched.SchedError as error:
                task_error = str(error)[:200]
        report = detach.assess(proc, state, task=task)
        if task_error:
            report["task_error"] = task_error
        _print(report)
        return 0
    if args.command == "stop":
        _print({"stop": str(detach.request_stop(proc, args.reason or "OPERATOR_STOP"))})
        return 0
    if args.command == "kill":
        launch_doc = detach.read_launch(proc)
        if launch_doc and launch_doc.get("mechanism") == sched.MECHANISM:
            _print(detach.kill_task(proc, confirm=args.confirm))
        else:
            _print(detach.kill(proc, confirm=args.confirm))
        return 0
    if args.command == "recover":
        from sim.runner import containment

        launch_doc = detach.read_launch(proc) or {}
        report = containment.recover(proc, sched.run_default, launch_doc.get("run_id") or args.run_id, confirm=args.confirm, grace_s=args.grace_s,
                                     anchors_dir=root / sched.ANCHOR_DIR)
        _print(report)
        return 0 if not report["blocked"] and report.get("owned_alive_after") == 0 else 1
    if args.command == "tail":
        for line in detach.tail(proc, args.stream, args.lines):
            print(line)
        return 0
    if not args.python or not args.repo or not args.instance:
        raise RunnerError("launch exige --python (interpretador estavel), --repo e --instance")
    ledger = RunLedger(run_dir)
    cli_args = f'run --run-id {args.run_id} --runs-root "{root}" --instance {args.instance}'
    env = {"AUNERON_SIM_HOME": args.sim_home or "", "PYTHONUTF8": "1", "PYTHONPATH": args.repo}
    info = {"executable": args.python}
    _print(detach.launch_detached(root, args.run_id, python=args.python, repo=args.repo, cli_args=cli_args,
                                  env={k: v for k, v in env.items() if v}, ledger_state=ledger.state, python_info=info,
                                  mechanism=args.mechanism, allow_rejected=args.allow_rejected_wmi, profile=args.profile))
    return 0


def _sched(args) -> int:
    from sim.runner import detach
    from sim.runner import detach_probe
    from sim.runner import sched
    from sim.runner.host import pid_alive

    root = _runs_root(args)
    if args.command == "sched-list":
        _print(sched.list_folder(sched.run_default))
        return 0
    if args.command == "sched-probe":
        return _sched_probe(args, root, detach_probe)
    proc = detach.proc_dir(root, args.run_id)
    if args.command == "sched-audit-verify":
        report = sched.Audit(proc, root / sched.ANCHOR_DIR, args.run_id).verify()
        _print(report)
        return 0 if report["ok"] else 1
    run_dir = root / args.run_id
    state = RunLedger(run_dir).state if run_dir.exists() else None
    from sim.runner import containment

    owned = containment.owned_summary(proc, sched.run_default)               # SIM-1.5G.4: processos do run por identidade
    report = sched.reconcile(sched.run_default, proc=proc, anchors_dir=root / sched.ANCHOR_DIR, name=args.run_id, ledger_state=state,
                             alive=pid_alive, apply=args.apply, confirm=args.confirm, markers=[args.run_id], owned=owned)
    report["owned"] = owned
    _print(report)
    return 0 if report["ok"] else 1


def _sched_probe(args, root: Path, detach_probe) -> int:
    from sim.runner import sched

    if args.level not in detach_probe.LEVELS:
        raise RunnerError("--level deve ser L1 ou L2")
    if args.phase == "launch":
        if not args.python or not args.repo:
            raise RunnerError("sched-probe launch exige --python (venv estavel) e --repo")
        name = args.name or detach_probe.probe_name(args.level)
        initiator, excluded = None, []
        if args.level == "L2":                                  # a vigilia do CLI iniciador e obrigatoria: ambiguidade => nada e registrado
            import os

            from sim.runner import watch

            def cmdline_of(pid):
                found = sched.process_chain(sched.run_default, pid)
                return (found[0].get("cmd") if found and found[0].get("pid") == pid else None)

            initiator, excluded = watch.discover_initiator(watch.ancestors(os.getpid()), cmdline_of)
        doc = detach_probe.probe_launch(root, name, level=args.level, python=args.python, repo=args.repo,
                                        interactive_session=detach_probe._session_id() or 0, initiator=initiator, excluded=excluded,
                                        helpers=args.watch_pid or ())
        _print({"name": name, "task": doc["task"], "heartbeat_pid": doc["heartbeat_pid"], "engine_pids": doc["engine_pids"],
                "initiator": ({k: initiator[k] for k in ("pid", "created", "iso", "resume_id")} if initiator else None),
                "excluded": excluded})
        return 0
    if not args.name:
        raise RunnerError("--name e obrigatorio em monitor/finish")
    if args.phase == "monitor":
        monitor = detach_probe.probe_monitor(root, args.name, level=args.level, session_end_t=args.session_end_epoch)
        _print({"name": args.name, "beats": monitor["beats"], "probe_alive_before_stop": monitor["probe_alive_before_stop"],
                "exit": monitor["exit"], "audit_ok": monitor["audit_check"]["ok"]})
        return 0
    if args.phase == "finish":
        doc = detach_probe.probe_finish(root, args.name, level=args.level)
        _print({"name": args.name, **doc["verdict"], "removal": doc["removal"], "orphans": len(doc["orphans"])})
        return 0 if doc["verdict"]["verdict"] == "PASS" else 1
    raise RunnerError("--phase deve ser launch, monitor ou finish")


def _live(args) -> int:
    from sim.live import env as E
    from sim.runner import nh_phases

    lab = E.open_lab(args.instance)
    if args.command == "run":
        nh_phases.apply_launch_env(nh_phases.detach.proc_dir(nh_phases.runs_root(), args.run_id))
    if args.command == "stage":
        result = nh_phases.phase_stage(lab, args.bundle)
    elif args.command == "open":
        result = nh_phases.phase_open(lab, args.bundle, Path(args.growth_model) if args.growth_model else None,
                                      args.purpose, args.parent, args.reason)
    elif args.command == "preflight":
        result = nh_phases.phase_preflight(lab, args.run_id)
    else:
        result = nh_phases.phase_run(lab, args.run_id)
    _print({"phase": f"nh-{args.command}", "status": result["status"]})
    return 0 if result["status"] == "PASS" else 1


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim.runner.cli")
    parser.add_argument("command", choices=[*OFFLINE, *PROCESS, *SCHED, *LIVE])
    parser.add_argument("--run-id")
    parser.add_argument("--runs-root")
    parser.add_argument("--digest")
    parser.add_argument("--by")
    parser.add_argument("--reference")
    parser.add_argument("--instance")
    parser.add_argument("--bundle", default="nh", choices=("nh", "sr1"))
    parser.add_argument("--growth-model")
    parser.add_argument("--purpose", default="FIRST", choices=("FIRST", "RETRY", "REPRODUCTION"))
    parser.add_argument("--parent")
    parser.add_argument("--reason")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--root")
    parser.add_argument("--allow", action="append")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--stream", default="stdout", choices=("stdout", "stderr"))
    parser.add_argument("--lines", type=int, default=50)
    parser.add_argument("--python")
    parser.add_argument("--repo")
    parser.add_argument("--sim-home")
    parser.add_argument("--mechanism", default="task_scheduler", choices=("task_scheduler", "wmi"))
    parser.add_argument("--allow-rejected-wmi", action="store_true", help="WMI foi REPROVADO no DETACH-1; so para reproducao")
    parser.add_argument("--profile", default="SR1", choices=("SR1", "NH_RUN"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--grace-s", type=float, default=20)
    parser.add_argument("--phase", choices=("launch", "monitor", "finish"))
    parser.add_argument("--level", default="L1")
    parser.add_argument("--name")
    parser.add_argument("--session-end-epoch", type=int, help="so evidencia historica legacy-1; na v2 o fim vem da vigilia duravel")
    parser.add_argument("--watch-pid", type=int, action="append", help="PID auxiliar (controle) a vigiar na sonda; repetivel")
    args = parser.parse_args(argv)
    if args.command in NEEDS_RUN_ID and not args.run_id:
        parser.error("--run-id e obrigatorio")
    if args.command == "contamination-scan" and not args.root:
        parser.error("--root e obrigatorio")
    if args.command in ("stage", "open", "preflight", "run") and not args.instance:
        parser.error("--instance e obrigatorio nas fases live")
    if args.command == "sched-probe" and not args.phase:
        parser.error("--phase e obrigatorio em sched-probe")
    if args.command == "authorize-rehearsal" and not (args.by and args.reference):
        parser.error("--by e --reference sao obrigatorios")
    try:
        if args.command in OFFLINE:
            return _offline(args)
        if args.command in SCHED:
            return _sched(args)
        return _process(args) if args.command in PROCESS else _live(args)
    except RunnerError as error:
        print(f"CONTRATO VIOLADO ({type(error).__name__}): {error}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
