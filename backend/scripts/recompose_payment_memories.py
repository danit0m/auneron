"""
Recomposicao UNICA e controlada das memorias de comportamento de pagamento
(memory_key='client_behavior_payment_pattern') gravadas antes da data oficial
de pagamento (account_payments.paid_on).

Script operacional, rodado manualmente (comando recompor-memorias-pagamento do
homologacao.ps1) a partir de backend/:
    python -m scripts.recompose_payment_memories simular
    python -m scripts.recompose_payment_memories confirmar

Pendente = memoria ATIVA de comportamento, escopo account, cujo context_data
nao tem 'ocorrencias_sem_data_efetiva' (marcador gravado pelo calculo novo).

Regras:
- A primeira coisa feita e conferir o banco (SELECT current_database()) na
  MESMA sessao que gravaria; se nao for o esperado, encerra sem escrever.
- Reaproveita apply_client_behavior_memory_pattern (mesmo calculo e mesmo
  supersede do loop de manutencao); nao duplica logica.
- Se o recalculo nao produz padrao (ciclos com data efetiva abaixo do
  minimo), a memoria antiga e ENCERRADA com MemoryService.invalidate (status
  'invalidated' + motivo obrigatorio, auditavel). Nada e apagado.
- Idempotente: apos a execucao nao ha pendentes (nova memoria tem o marcador;
  memoria encerrada deixa de ser ativa). Segunda execucao: "0 pendentes".
- simular nao grava nada (transacao revertida).
- E-mails sao sempre mascarados na saida.

Codigos de saida:
    0  concluido (inclui 0 pendentes)
    1  falha ao recompor pelo menos uma memoria (as demais seguem)
    2  uso invalido
    4  banco diferente do esperado (nada gravado)
"""

import sys
from dataclasses import dataclass
from dataclasses import field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.client_behavior_memory_maintenance import MEMORY_KEY
from app.core.client_behavior_memory_maintenance import (
    apply_client_behavior_memory_pattern,
)
from app.core.client_behavior_memory_maintenance import (
    compute_client_behavior_pattern,
)
from app.models.memory import MemoryItem
from app.services.memory_service import MemoryService

EXPECTED_DATABASE = "auneron_neon_synthetic"

MODE_SIMULATE = "simular"
MODE_CONFIRM = "confirmar"
MODES = (MODE_SIMULATE, MODE_CONFIRM)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_WRONG_DATABASE = 4

CLOSE_REASON = "recomposicao_data_efetiva: ciclos insuficientes"
REPLACED_REASON = (
    "recomposicao_data_efetiva: substituida por memoria de outra conta"
)

PENDING_SQL = text(
    """
    SELECT mi.id AS memory_id, a.email AS email
    FROM memory_items mi
    JOIN accounts a ON a.id = mi.account_id
    WHERE mi.memory_key = :memory_key
      AND mi.scope_type = 'account'
      AND mi.status = 'active'
      AND a.email IS NOT NULL
      AND (mi.context_data ->> 'ocorrencias_sem_data_efetiva') IS NULL
    ORDER BY mi.id
    """
)


@dataclass
class Summary:
    pending: int = 0
    recomposed: int = 0
    closed: int = 0
    failed: int = 0
    recomposed_emails: list[str] = field(default_factory=list)
    closed_emails: list[str] = field(default_factory=list)
    failed_emails: list[str] = field(default_factory=list)


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:3]}***@{domain}"


def _pending(db: Session) -> list[tuple[int, str]]:
    rows = db.execute(PENDING_SQL, {"memory_key": MEMORY_KEY}).all()
    return [(row.memory_id, row.email) for row in rows]


def _print_list(title: str, emails: list[str]) -> None:
    print(f"  {title}: {len(emails)}")
    for email in emails:
        print(f"    - {mask_email(email)}")


def _simulate(db: Session, rows: list[tuple[int, str]]) -> Summary:
    summary = Summary(pending=len(rows))
    for _memory_id, email in rows:
        if compute_client_behavior_pattern(db, email) is None:
            summary.closed += 1
            summary.closed_emails.append(email)
        else:
            summary.recomposed += 1
            summary.recomposed_emails.append(email)
    db.rollback()
    return summary


def _recompose(db: Session, rows: list[tuple[int, str]]) -> Summary:
    summary = Summary(pending=len(rows))
    service = MemoryService(db)
    for memory_id, email in rows:
        try:
            result = apply_client_behavior_memory_pattern(
                db, service, email
            )
            db.expire_all()
            old = db.get(MemoryItem, memory_id)
            if result is None:
                service.invalidate(memory_id, reason=CLOSE_REASON)
                summary.closed += 1
                summary.closed_emails.append(email)
                continue
            if old is not None and old.status == "active":
                # Padrao criado em outra conta (a mais antiga mudou): a
                # memoria pendente nao pode continuar valendo.
                service.invalidate(memory_id, reason=REPLACED_REASON)
            summary.recomposed += 1
            summary.recomposed_emails.append(email)
        except Exception as error:
            db.rollback()
            summary.failed += 1
            summary.failed_emails.append(email)
            print(
                f"  FALHA em {mask_email(email)}: "
                f"{type(error).__name__}"
            )
    return summary


def run(
    db: Session,
    mode: str = MODE_SIMULATE,
    expected_database: str = EXPECTED_DATABASE,
) -> int:
    database = db.execute(text("SELECT current_database()")).scalar()
    if database != expected_database:
        db.rollback()
        print(
            f"RECUSADO: banco conectado e '{database}', esperado "
            f"'{expected_database}'. Nada foi gravado."
        )
        return EXIT_WRONG_DATABASE
    if mode not in MODES:
        db.rollback()
        print(f"ERRO: modo desconhecido '{mode}'. Use simular ou confirmar.")
        return EXIT_USAGE

    print(f"  banco: {database}")
    rows = _pending(db)
    if not rows:
        db.rollback()
        print("0 pendentes. Nada foi alterado.")
        return EXIT_OK

    if mode == MODE_SIMULATE:
        summary = _simulate(db, rows)
        print(
            f"SIMULACAO: {summary.pending} pendente(s); nada foi gravado."
        )
        _print_list("seriam recompostas", summary.recomposed_emails)
        _print_list(
            "seriam encerradas (ciclos insuficientes)",
            summary.closed_emails,
        )
        return EXIT_OK

    summary = _recompose(db, rows)
    print(f"{summary.pending} pendente(s) processada(s).")
    _print_list("recompostas", summary.recomposed_emails)
    _print_list(
        "encerradas (invalidated, ciclos insuficientes; nada apagado)",
        summary.closed_emails,
    )
    _print_list("falhas", summary.failed_emails)
    return EXIT_FAILURE if summary.failed else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in MODES:
        print(
            "Uso: python -m scripts.recompose_payment_memories "
            "[simular|confirmar]"
        )
        return EXIT_USAGE

    from app.database.database import SessionLocal

    db = SessionLocal()
    try:
        return run(db, args[0])
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
