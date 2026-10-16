"""
Autoridade FORA de C-AUTH-2 (D-1.5E-5, Tier 1 + Tier 2). NENHUM segredo do Oracle entra aqui.

* Tier 1 (factual, sem Oracle): `status_ledger` (todo status nao-2xx) e `authority_chain` (ator -> operacao ->
  resultado), DERIVADOS das linhas que o Driver ja grava (`kind=http` e `kind=op_result`). Nao muda o Driver.
* Tier 2 (tripwire no host, sem Oracle): so INVARIANTES PUBLICOS de autoridade, aplicados a SUCESSOS (2xx):
    - passo governado por ator sem aquela autoridade;
    - segregacao (decisor == solicitante ou executor do mesmo pedido);
    - cadeia completa fora das duas cadeias publicas (padrao/reserva).
  Rejeicoes (4xx) NAO sao julgadas aqui: o conjunto de rejeicoes esperadas (as tentativas de C-AUTH-2) so existe no
  Oracle e a adjudicacao completa e OFFLINE (Evaluator/`verify_authority`). O tripwire nao recebe Oracle, verdade,
  IDs de caso nem expectativa; recebe a agenda PUBLICA e as linhas de evidencia do Driver.
"""

from __future__ import annotations

# --- invariantes PUBLICOS (espelham sim/company/nova_horizonte/v2/company.yaml; um teste confere o espelho) ------------
PERSONA_AUTHORITY = {
    "sim-faturamento": frozenset(), "sim-receber": frozenset(),
    "sim-cobranca-1": frozenset({"human_assessment"}), "sim-cobranca-2": frozenset({"human_assessment"}),
    "sim-cobranca-3": frozenset({"human_assessment"}),
    "sim-gerente-fin": frozenset({"request", "decide", "execute"}),
    "sim-coordenador-fin": frozenset({"request", "decide", "execute", "escalate", "consult"}),
    "sim-controller": frozenset(), "sim-admin": frozenset(),
}
CHAINS = (
    {"requester": "sim-gerente-fin", "decider": "sim-coordenador-fin", "executor": "sim-gerente-fin"},       # padrao
    {"requester": "sim-coordenador-fin", "decider": "sim-gerente-fin", "executor": "sim-coordenador-fin"},    # reserva
)
STEP_BY_OP = {
    "request_mark_paid": "request", "materialize_mark_overdue": "request",
    "decide_mark_paid": "decide", "decide_mark_overdue": "decide",
    "execute_mark_paid": "execute", "execute_mark_overdue": "execute",
    "materialize_escalation": "escalate", "consult_nba": "consult", "record_assessment": "human_assessment",
}
GOVERNED_STEPS = ("request", "decide", "execute")

V_UNAUTHORIZED = "STEP_BY_UNAUTHORIZED_ACTOR"
V_SEGREGATION = "SEGREGATION_BREACH"
V_CHAIN = "CHAIN_OUTSIDE_PUBLIC_CHAINS"


def _success(line: dict) -> bool:
    status = line.get("http_status")
    return isinstance(status, int) and 200 <= status < 300 and line.get("outcome") in ("done", "reconciled")


def derive_status_ledger(lines: list) -> list:
    """Toda resposta HTTP nao-2xx (ou erro de transporte) que o Driver registrou. Sem julgamento."""
    out = []
    for line in lines:
        if line.get("kind") != "http":
            continue
        status = line.get("status")
        if isinstance(status, int) and 200 <= status < 300:
            continue
        out.append({"n": line.get("n"), "seq": line.get("seq"), "day": line.get("day"), "at": line.get("at"),
                    "op": line.get("op"), "actor": line.get("actor"), "method": line.get("method"),
                    "path": line.get("path"), "status": status, "attempt": line.get("attempt")})
    return out


def derive_authority_chain(lines: list) -> list:
    """Cadeia factual ator -> operacao -> resultado (uma linha por `op_result`)."""
    return [{"n": line.get("n"), "seq": line.get("seq"), "day": line.get("day"), "at": line.get("at"),
             "op": line.get("op"), "actor": line.get("actor"), "ref": line.get("ref"),
             "http_status": line.get("http_status"), "outcome": line.get("outcome")}
            for line in lines if line.get("kind") == "op_result"]


class AuthorityTripwire:
    """Estado incremental: alimentado dia a dia com as NOVAS linhas do Driver. Devolve violacoes publicas."""

    def __init__(self) -> None:
        self.steps: dict = {}                 # ref -> {"request": actor, "decide": actor, "execute": actor} (so sucessos)
        self.fed = 0

    def feed(self, op_results: list) -> list:
        violations = []
        for line in op_results:
            if line.get("kind", "op_result") != "op_result" or not _success(line):
                continue
            step = STEP_BY_OP.get(line.get("op"))
            if step is None:
                continue
            actor = line.get("actor")
            self.fed += 1
            if step is not None and step not in PERSONA_AUTHORITY.get(actor, frozenset()):
                violations.append(_violation(V_UNAUTHORIZED, line, f"{actor} nao tem a autoridade `{step}`"))
                continue
            ref = line.get("ref")
            if step in GOVERNED_STEPS and ref:
                done = self.steps.setdefault(ref, {})
                done[step] = actor
                if step == "decide" and actor in (done.get("request"), done.get("execute")):
                    violations.append(_violation(V_SEGREGATION, line, f"decisor {actor} = solicitante/executor de {ref}"))
                if step == "execute" and actor == done.get("decide"):
                    violations.append(_violation(V_SEGREGATION, line, f"executor {actor} = decisor de {ref}"))
                if len(done) == 3 and not any(
                        (done["request"], done["decide"], done["execute"]) == (c["requester"], c["decider"], c["executor"])
                        for c in CHAINS):
                    violations.append(_violation(V_CHAIN, line, f"{ref}: {done} fora das cadeias publicas"))
        return violations


def _violation(kind: str, line: dict, detail: str) -> dict:
    return {"violation": kind, "seq": line.get("seq"), "day": line.get("day"), "at": line.get("at"),
            "op": line.get("op"), "actor": line.get("actor"), "ref": line.get("ref"), "detail": detail}


def public_agenda_check(ops: list) -> list:
    """Sanidade do proprio tripwire: se TODA op da agenda tivesse sucesso, o que ele acusaria? As tentativas de
    C-AUTH-2 aparecem como ops comuns da agenda (e o produto as rejeita); o helper mostra que SO elas seriam acusadas
    (nenhum passo legitimo) e que o tripwire nao precisa conhecer o Oracle para isso."""
    tripwire = AuthorityTripwire()
    lines = [{"kind": "op_result", "seq": o["seq"], "day": o["day"], "at": o["at"], "op": o["op"], "actor": o["actor"],
              "ref": o.get("ref"), "http_status": 200, "outcome": "done"} for o in ops]
    return tripwire.feed(lines)
