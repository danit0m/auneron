"""Auditoria de entrada de dados em accounts (API manual e CSV legado).

O helper so faz db.add: nunca flush, commit ou rollback. A linha de
auditoria entra na mesma transacao da escrita de negocio; se o INSERT ou
um CHECK falhar, o rollback do chamador desfaz as duas.

Digests: SHA-256 sem chave, com separacao de dominio, sobre o valor
persistido. Provam igualdade/mudanca e evitam leitura casual; NAO sao
anonimos nem cifrados (e-mail/telefone sao recuperaveis por dicionario).
"""
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.orm import Session

from app.core.observability import get_request_id
from app.models.account import Account
from app.models.account_data_entry_audit_event import (
    AccountDataEntryAuditEvent,
)


DIGEST_DOMAIN = b"auneron:account-data-entry-audit:v1:"
AUDITED_FIELDS = ("cliente", "email", "valor", "vencimento", "whatsapp")
DIGEST_FIELDS = ("cliente", "email", "whatsapp")
CHANNELS = ("manual_api", "legacy_csv_upload")


class AuditActorRequired(RuntimeError):
    # Deliberadamente nao e ValueError: upload.py mapeia ValueError para 400.
    pass


@dataclass(frozen=True)
class AuditActor:
    user_id: int
    auth_session_id: int | None


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


def audit_actor_from_ids(user_id, auth_session_id=None) -> AuditActor:
    # Sem fallback de sistema: ausente, bool ou <= 0 falha antes da escrita.
    if not _positive_int(user_id):
        raise AuditActorRequired("Ator humano autenticado obrigatorio.")
    if auth_session_id is not None and not _positive_int(auth_session_id):
        raise AuditActorRequired("Sessao autenticada invalida.")
    return AuditActor(user_id=user_id, auth_session_id=auth_session_id)


def audit_actor(authenticated) -> AuditActor:
    user = getattr(authenticated, "user", None)
    session = getattr(authenticated, "session", None)
    return audit_actor_from_ids(
        getattr(user, "id", None),
        getattr(session, "id", None),
    )


def field_digest(field: str, value) -> str | None:
    if value is None:
        return None
    if field not in DIGEST_FIELDS or not isinstance(value, str):
        raise TypeError("campo sem digest definido")
    return sha256(
        DIGEST_DOMAIN + field.encode("ascii") + b"\x00" + value.encode("utf-8")
    ).hexdigest()


def snapshot(account: Account) -> dict:
    values = {field: getattr(account, field) for field in AUDITED_FIELDS}
    values["status"] = account.status
    return values


def _side(prefix: str, values: dict | None) -> dict:
    values = values or {}
    columns = {
        f"{prefix}_valor": values.get("valor"),
        f"{prefix}_vencimento": values.get("vencimento"),
    }
    for field in DIGEST_FIELDS:
        columns[f"{prefix}_{field}_sha256"] = field_digest(field, values.get(field))
    return columns


def _add(db, operation, channel, account_id, status, actor, changed, before, after,
         source_sha256=None, source_row=None) -> AccountDataEntryAuditEvent:
    if not isinstance(actor, AuditActor):
        raise AuditActorRequired("Ator humano autenticado obrigatorio.")
    if channel not in CHANNELS:
        raise RuntimeError("canal de auditoria desconhecido")
    if not _positive_int(account_id):
        raise RuntimeError("conta sem id persistido")
    event = AccountDataEntryAuditEvent(
        operation=operation,
        channel=channel,
        account_reference=account_id,
        actor_type="user",
        actor_user_id=actor.user_id,
        actor_reference=f"user:{actor.user_id}",
        auth_session_id=actor.auth_session_id,
        request_id=get_request_id(),
        changed_fields=sorted(changed),
        status_at_operation=status,
        source_sha256=source_sha256,
        source_row=source_row,
        **_side("before", before),
        **_side("after", after),
    )
    db.add(event)
    return event


def record_create(db: Session, account: Account, actor: AuditActor, *, channel: str,
                  source_sha256: str | None = None, source_row: int | None = None):
    # Exige account.id: o chamador faz o flush da conta antes.
    after = snapshot(account)
    changed = [field for field in AUDITED_FIELDS if after[field] is not None]
    return _add(db, "create", channel, account.id, after["status"], actor, changed,
                None, after, source_sha256, source_row)


def record_update(db: Session, before: dict, account: Account, actor: AuditActor) -> bool:
    after = snapshot(account)
    changed = [field for field in AUDITED_FIELDS if before[field] != after[field]]
    if not changed:
        return False
    _add(db, "update", "manual_api", account.id, after["status"], actor, changed,
         before, after)
    return True


def record_delete(db: Session, account: Account, actor: AuditActor):
    before = snapshot(account)
    return _add(db, "delete", "manual_api", account.id, before["status"], actor, [],
                before, None)
