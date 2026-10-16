"""
Adjudicacao OFFLINE de status HTTP e autoridade (SIM-1.5E B.9, pos-run). Recebe DADOS (o livro-razao factual do
Driver e a lista de status esperados que o harness extraiu do Oracle); este modulo NAO importa o Oracle.

Regras sobre o `status_ledger`:
* 401 / erro de transporte / 5xx de um `seq` cuja resposta final foi 2xx => RECUPERADO pelo protocolo do Driver (relogin,
  retry): nao e anomalia;
* qualquer outro nao-2xx precisa casar com UM status esperado (dia, hora, {status possiveis}); cada esperado e
  consumido uma vez;
* sobra = INEXPLICADO => `UNEXPECTED_STATUS_UNEXPLAINED` (INVALID), nunca absorvido.
"""

from __future__ import annotations


def adjudicate_status(status_ledger: list, authority_chain: list, expected: list) -> dict:
    final = {}
    for row in authority_chain:
        final[row.get("seq")] = row.get("http_status")
    pool = [(e["day"], e["at"], tuple(e["statuses"])) for e in expected]
    recovered, matched, unexplained = [], [], []
    for row in status_ledger:
        status, seq = row.get("status"), row.get("seq")
        recoverable = status is None or status == 401 or (isinstance(status, int) and status >= 500)
        final_status = final.get(seq)
        if recoverable and isinstance(final_status, int) and 200 <= final_status < 300:
            recovered.append(row)
            continue
        hit = next((entry for entry in pool if entry[0] == row.get("day") and entry[1] == row.get("at")
                    and status in entry[2]), None)
        if hit is not None:
            pool.remove(hit)
            matched.append(row)
        else:
            unexplained.append(row)
    return {"ok": not unexplained, "recovered": len(recovered), "expected_matched": len(matched),
            "unexplained": unexplained, "expected_unmatched": [[d, a, list(st)] for d, a, st in pool]}
