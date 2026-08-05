from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
)

from app.core.money import money_to_json_number


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
    status: str = Field(
        default="aberto",
        max_length=30,
    )


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
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
    status: str | None = Field(
        default=None,
        max_length=30,
    )


class AccountResponse(AccountBase):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    created_at: datetime | None = None
