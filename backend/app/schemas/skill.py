from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict


class SkillAPISchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class SkillInvokeRequest(SkillAPISchema):
    input_payload: Any


class SkillInvocationResponse(SkillAPISchema):
    invocation_id: int
    skill_version_id: int
    status: str
    duplicate: bool
    output: Any
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
