from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AccountBase(BaseModel):
    cliente: str = Field(min_length=2, max_length=150)
    email: EmailStr | None = None
    whatsapp: str | None = Field(default=None, max_length=30)
    valor: float = Field(gt=0)
    vencimento: date
    status: str = Field(default="aberto", max_length=30)


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    cliente: str | None = Field(default=None, min_length=2, max_length=150)
    email: EmailStr | None = None
    whatsapp: str | None = Field(default=None, max_length=30)
    valor: float | None = Field(default=None, gt=0)
    vencimento: date | None = None
    status: str | None = Field(default=None, max_length=30)


class AccountResponse(AccountBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime | None = None