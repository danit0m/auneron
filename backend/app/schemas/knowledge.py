from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class KnowledgeBase(BaseModel):
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

    severity: str = Field(
        default="info",
        min_length=2,
        max_length=30,
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
    id: int
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class KnowledgeResolveResponse(BaseModel):
    id: int
    resolved: bool
    message: str
