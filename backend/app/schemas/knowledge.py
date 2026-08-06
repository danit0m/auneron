from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


KnowledgeSeverity = Literal[
    "critical",
    "high",
    "medium",
    "info",
]


class KnowledgeBase(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    agent_name: str = Field(
        ...,
        min_length=2,
        max_length=100,
    )

    event_name: str = Field(
        ...,
        min_length=2,
        max_length=100,
    )

    knowledge_type: str = Field(
        default="insight",
        min_length=2,
        max_length=50,
    )

    severity: KnowledgeSeverity = Field(
        default="info",
    )

    title: str = Field(
        ...,
        min_length=2,
        max_length=200,
    )

    message: str = Field(
        ...,
        min_length=2,
    )

    account_id: int | None = None
    resolved: bool = False


class KnowledgeCreate(KnowledgeBase):
    pass


class KnowledgeResponse(KnowledgeBase):
    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
    )

    id: int
    created_at: datetime


class KnowledgeResolveResponse(BaseModel):
    id: int
    resolved: bool
    message: str