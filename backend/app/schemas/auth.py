from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import EmailStr
from pydantic import Field


UserRole = Literal[
    "viewer",
    "analyst",
    "manager",
    "executive",
    "administrator",
    "developer",
]


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    email: EmailStr
    password: str = Field(
        min_length=1,
        max_length=128,
    )


class ElevationRequest(BaseModel):
    password: str = Field(
        min_length=1,
        max_length=128,
    )


class AuthUserResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    name: str
    email: str
    role: UserRole
    active: bool


class AuthSessionResponse(BaseModel):
    user: AuthUserResponse
    authenticated_at: datetime
    expires_at: datetime
    elevated_until: datetime | None


class ElevationResponse(BaseModel):
    elevated_until: datetime
