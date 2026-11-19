import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from app.core.money import money_to_json_number
from app.core.receivable_lifecycle import ReceivableLifecycleState
from app.core.receivable_lifecycle import business_today
from app.core.receivable_lifecycle import evaluate_receivable_lifecycle


AccountStatus = Literal[
    "aberto",
    "atrasado",
    "pago",
]


Money = Annotated[
    Decimal,
    Field(
        gt=Decimal("0"),
        max_digits=14,
        decimal_places=2,
    ),
    PlainSerializer(
        money_to_json_number,
        return_type=float,
        when_used="json",
    ),
]


class AccountBase(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    cliente: str = Field(
        min_length=2,
        max_length=150,
    )

    email: EmailStr | None = None

    whatsapp: str | None = Field(
        default=None,
        max_length=30,
    )

    valor: Money
    vencimento: date

    status: AccountStatus = Field(
        default="aberto",
    )


# TASK054: a referencia de titulo so entra pela rota de atribuicao. Em
# POST/PUT /accounts estas chaves sao recusadas (422); as demais chaves
# desconhecidas continuam ignoradas como antes (sem extra="forbid").
TITLE_REFERENCE_KEYS = frozenset({"title_source", "title_reference"})


def _reject_title_reference_keys(data):
    if isinstance(data, dict) and TITLE_REFERENCE_KEYS.intersection(data):
        raise ValueError("title_reference_fields_not_accepted")
    return data


class AccountCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    cliente: str = Field(min_length=2, max_length=150)
    email: EmailStr | None = None
    whatsapp: str | None = Field(default=None, max_length=30)
    valor: Money
    vencimento: date

    @model_validator(mode="before")
    @classmethod
    def _no_title_reference(cls, data):
        return _reject_title_reference_keys(data)


class AccountUpdate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    cliente: str | None = Field(
        default=None,
        min_length=2,
        max_length=150,
    )

    email: EmailStr | None = None

    whatsapp: str | None = Field(
        default=None,
        max_length=30,
    )

    valor: Money | None = None
    vencimento: date | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_title_reference(cls, data):
        return _reject_title_reference_keys(data)


# Fatia "Contato por cliente": completa e-mail/WhatsApp de todos os titulos de
# um cliente. A regra de WhatsApp vale so aqui (AccountCreate/AccountUpdate nao
# mudam) e o frontend (lib/contatoCliente.ts) espelha a mesma regra.
_WHATSAPP_SEPARATORS = re.compile(r"[\s\-()]")
_WHATSAPP_NORMALIZED = re.compile(r"^\+?[0-9]{10,15}$")


def normalize_whatsapp(value: str) -> str:
    cleaned = _WHATSAPP_SEPARATORS.sub("", value)
    if not _WHATSAPP_NORMALIZED.fullmatch(cleaned):
        raise ValueError(
            "WhatsApp inválido: use apenas números, com + opcional no "
            "início, de 10 a 15 dígitos (espaços, hífens e parênteses "
            "são aceitos)."
        )
    return cleaned


class ClientContactUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    cliente: str = Field(min_length=2, max_length=150)
    email: EmailStr | None = None
    whatsapp: str | None = None

    @field_validator("whatsapp")
    @classmethod
    def _normalize_whatsapp(cls, value):
        # Branco equivale a ausente (nao altera o campo).
        if value is None or value == "":
            return None
        return normalize_whatsapp(value)

    @model_validator(mode="after")
    def _require_some_contact(self) -> "ClientContactUpdate":
        if self.email is None and self.whatsapp is None:
            raise ValueError(
                "Informe ao menos um contato: e-mail ou WhatsApp."
            )
        return self


class ClientContactUpdateResponse(BaseModel):
    cliente: str
    matched: int
    updated: int


class ReceivableLifecycle(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    state: ReceivableLifecycleState
    days_to_due: int
    days_overdue: int
    as_of: date


class AccountResponse(AccountBase):
    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
    )

    id: int
    created_at: datetime

    # A3 -- data efetiva do pagamento (tabela lateral account_payments);
    # None para titulo nao pago ou pago antes desta fatia.
    paid_on: date | None = None

    # A5 -- agregado de contato/promessa (tabela lateral
    # account_contact_events), calculado na leitura e preenchido em lote
    # pela rota; nunca aceito como entrada.
    ultimo_contato_em: datetime | None = None
    promessa_vigente: date | None = None
    promessa_vencida: bool = False

    # F3 -- projecao advisory/read-only calculada a cada resposta, nunca
    # aceita como entrada (AccountCreate/AccountUpdate nao possuem este
    # campo). Sempre recalculada pelo model_validator abaixo, mesmo se
    # o objeto de origem tiver um atributo com este nome.
    receivable_lifecycle: ReceivableLifecycle | None = None

    @model_validator(mode="after")
    def _compute_receivable_lifecycle(self) -> "AccountResponse":
        lifecycle = evaluate_receivable_lifecycle(
            financial_status=self.status,
            vencimento=self.vencimento,
            today=business_today(),
        )

        self.receivable_lifecycle = ReceivableLifecycle(
            state=lifecycle.state,
            days_to_due=lifecycle.days_to_due,
            days_overdue=lifecycle.days_overdue,
            as_of=lifecycle.as_of,
        )

        return self


AccountContactEventType = Literal[
    "contato",
    "promessa",
    "remarcacao",
    "encerramento",
]


class AccountContactEventCreate(BaseModel):
    """A5 -- registro de contato/promessa (so inclusao)."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    event_type: AccountContactEventType
    promised_date: date | None = None
    note: str | None = Field(default=None, max_length=280)

    @field_validator("note")
    @classmethod
    def _blank_note_is_absent(cls, value):
        return value or None

    @model_validator(mode="after")
    def _promised_date_matches_type(self) -> "AccountContactEventCreate":
        needs_date = self.event_type in ("promessa", "remarcacao")
        if needs_date and self.promised_date is None:
            raise ValueError("promised_date_required")
        if not needs_date and self.promised_date is not None:
            raise ValueError("promised_date_not_allowed")
        return self


class AccountContactEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    event_type: AccountContactEventType
    promised_date: date | None
    note: str | None
    actor_user_id: int
    created_at: datetime


# TASK054 (TI1): atribuicao manual da referencia de origem a um titulo
# legado. Strict e extra="forbid" so neste schema novo. A rota valida o
# corpo por conta propria e responde 422 com codigo fixo, sem ecoar valores;
# a normalizacao (strip, NFC, regras) fica em account_title_reference.
class TitleReferenceAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title_source: str
    title_reference: str


class TitleReferenceWarning(BaseModel):
    code: Literal["coincident_referenced_tuple"]
    account_ids: list[int]


class TitleReferenceAssignResponse(BaseModel):
    account_id: int
    title_source: str
    title_reference: str
    warnings: list[TitleReferenceWarning]


AccountMarkPaidExpectedStatus = Literal[
    "aberto",
    "atrasado",
]


class AccountMarkPaidExecuteRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    approval_request_id: int = Field(gt=0)
    expected_status: AccountMarkPaidExpectedStatus


class AccountMarkPaidRequest(BaseModel):
    """A2/A3 -- pedido de registro de pagamento com data efetiva."""

    model_config = ConfigDict(
        extra="forbid",
    )

    paid_on: date


# Fatia 2B -- leitura read-only da classificacao ja calculada e
# persistida pela Fatia 2A (app/core/client_classification.py). Nao
# recalcula, nao cria, nao altera -- so expoe o que ja existe.
ClientClassificationLabel = Literal[
    "PAGAMENTO_REGULAR",
    "ATRASO_RECORRENTE",
    "INSUFFICIENT_DATA",
]


ClientClassificationStatus = Literal[
    "not_classified_yet",
    "classified",
]


class ClientClassificationDetail(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    label: ClientClassificationLabel
    reason: str | None = None
    rule_version: str
    classified_at: datetime
    resolved_occurrences: int
    late_occurrences: int | None = None
    late_ratio: float | None = None
    minimum_required_occurrences: int
    late_ratio_threshold: float
    analysis_scope: str
    # Titulos pagos sem data efetiva (fora do calculo). None = memoria
    # gravada antes da fonte oficial de data de pagamento.
    occurrences_without_effective_date: int | None = None
    period_start: date | None = None
    period_end: date | None = None


class AccountClassificationResponse(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    account_id: int
    email: EmailStr | None = None
    status: ClientClassificationStatus
    classification: ClientClassificationDetail | None = None
