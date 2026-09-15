from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
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


class AccountCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    cliente: str = Field(min_length=2, max_length=150)
    email: EmailStr | None = None
    whatsapp: str | None = Field(default=None, max_length=30)
    valor: Money
    vencimento: date


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
