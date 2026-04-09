from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CandidateApplicationFieldInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(min_length=1, max_length=100)
    value: Any = None
    display_value: str | None = None
    asset_id: int | None = None


class CandidateApplicationSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CandidateApplicationFieldInput] = Field(default_factory=list)


class CandidateApplicationSubmitResponse(BaseModel):
    application_id: int
    talent_profile_id: int
    talent_profile_created: bool = False
    auto_merged: bool = False


class CandidateApplicationSummaryRead(BaseModel):
    id: int
    job_id: int
    job_snapshot_title: str
    job_snapshot_company_name: str | None = None
    status: str
    status_cn_name: str
    current_stage: str | None = None
    current_stage_cn_name: str | None = None
    submitted_at: datetime
    source_of_current_snapshot: bool = False
