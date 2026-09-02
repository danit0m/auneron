from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
)

from app.core.money import money_to_json_number


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


class AccountResponse(AccountBase):
    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
    )

    id: int
    created_at: datetime