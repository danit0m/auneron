from dataclasses import dataclass
from datetime import date
from datetime import datetime
from typing import Iterable
from typing import Protocol


class ContactEventLike(Protocol):
    event_type: str
    promised_date: date | None
    created_at: datetime | None


@dataclass(frozen=True)
class ContactSummary:
    ultimo_contato_em: datetime | None
    promessa_vigente: date | None
    promessa_vencida: bool


EMPTY_SUMMARY = ContactSummary(None, None, False)


def summarize_contact_events(
    events: Iterable[ContactEventLike],
    *,
    account_status: str,
    today: date,
) -> ContactSummary:
    """
    Regra calculada na leitura (A5). `events` em ordem cronologica
    (do mais antigo ao mais recente).

    - ultimo contato: data do ultimo evento contato/promessa/remarcacao
      (o encerramento nao e um contato com o cliente).
    - promessa vigente: o ultimo evento entre promessa/remarcacao/
      encerramento, quando for promessa ou remarcacao.
    - promessa vencida: promessa vigente com data anterior a hoje (fuso
      de negocio) e titulo nao pago. Hoje ainda nao esta vencida.
    """
    ultimo_contato: datetime | None = None
    vigente: date | None = None

    for event in events:
        if event.event_type in ("contato", "promessa", "remarcacao"):
            ultimo_contato = event.created_at
        if event.event_type in ("promessa", "remarcacao"):
            vigente = event.promised_date
        elif event.event_type == "encerramento":
            vigente = None

    vencida = (
        vigente is not None
        and vigente < today
        and account_status != "pago"
    )
    return ContactSummary(ultimo_contato, vigente, vencida)
