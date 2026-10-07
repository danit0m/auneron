"""
CLI do harness live (uma fase por comando; saida: a evidencia fica em `<lab_home>/<instancia>/evidence.json`):

    python -m sim.live.cli <fase> --instance <id> [--new]

Sessao tipica (C0b e regressao DR), cada uma num stack DESCARTAVEL proprio:

  prep -> up-base -> provision -> compose-config -> build -> up-ext -> stage-secrets -> c0b -> reset
  prep -> up-base -> provision -> compose-config -> up-ext -> stage-base -> dr1 -> c0 -> dr2 -> dr3 -> dr4
       -> dr5 -> dr6 -> dr7 -> dr8 -> teardown
  # sessao 3 (D90)   stack descartavel proprio: restart real + barreira extraordinaria + Collector
  prep -> up-base -> provision -> compose-config -> up-ext -> stage-secrets -> d90 -> teardown

`prep` guarda o manifesto aprovado do candidato (indice) e o HEAD esperado; `teardown` reconcilia contra eles.
Fase com FAIL => o operador PARA e leva a evidencia ao PO; nenhuma fase corrige o ambiente sozinha.
GuardViolation => ABORT (exit 3).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from sim.live import env as E
from sim.live import flow
from sim.live import gates as G
from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.quiescence import HarnessError
from sim.stack.lab.runner import Runner

PHASES = {
    "prep": G.phase_prep, "up-base": G.phase_up_base, "provision": G.phase_provision,
    "compose-config": G.phase_compose_config, "build": G.phase_build_images, "up-ext": G.phase_up_ext,
    "stage-secrets": lambda lab: _ok(lab, "LIVE-STAGE-SECRETS", flow.stage_secrets),
    "stage-base": G.phase_stage_base, "dr1": G.phase_dr1, "c0": G.phase_c0, "dr2": G.phase_dr2, "dr3": G.phase_dr3,
    "dr4": G.phase_dr4, "dr5": G.phase_dr5, "dr6": G.phase_dr6, "dr7": G.phase_dr7, "dr8": G.phase_dr8,
    "c0b": G.phase_c0b, "d90": G.phase_d90, "reset": G.phase_reset, "teardown": G.phase_teardown,
}


def _ok(lab, gate, fn):
    fn(lab)
    return lab.record(gate, True, {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim.live.cli")
    parser.add_argument("phase", choices=[*PHASES, "candidate-manifest", "summary"])
    parser.add_argument("--instance", default=None)
    parser.add_argument("--new", action="store_true")
    parser.add_argument("--out", default=None, help="candidate-manifest: arquivo de saida (fora do repositorio)")
    args = parser.parse_args(argv)
    if args.instance is None and args.phase not in ("prep", "candidate-manifest"):
        parser.error("--instance e obrigatorio (use o id impresso pela fase `prep`)")
    if args.phase == "candidate-manifest":              # so le o indice do git: nao cria instancia do lab
        rows = G.candidate_manifest(SimpleNamespace(runner=Runner()))
        if args.out:
            Path(args.out).write_text(G.manifest_text(rows), encoding="utf-8", newline="\n")
        print(json.dumps({"files": len(rows), "digest": G.manifest_digest(rows)}))
        return 0
    instance = args.instance or E.new_instance_id()
    try:
        lab = E.open_lab(instance, new=args.new)
        if args.phase == "summary":
            print(json.dumps({k: v["status"] for k, v in sorted(lab.evidence["gates"].items())}, indent=1))
            return 0
        result = PHASES[args.phase](lab)
    except GuardViolation as error:
        print(f"ABORT (guarda): {error}")
        return 3
    except (HarnessError, flow.ExecError) as error:
        print(f"HARNESS_ERROR: {error}")
        return 4
    print(json.dumps({"phase": args.phase, "instance_id": lab.instance_id, "status": result["status"]}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
