from typing import Any
import re

from pydantic import BaseModel, Field, field_validator


class UploadResponse(BaseModel):
    video_id: str


class AnalyzeRequest(BaseModel):
    video_id: str


class AnalyzeResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    video_id: str
    status: str
    progress: float
    error: str | None = None


class PromptRequest(BaseModel):
    target: str = Field(default="all", min_length=1, max_length=80)

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("target is required")

        if value in {"all", "sora", "seedance", "script"}:
            return value

        if len(value) > 80:
            raise ValueError("target must be 80 chars or less")

        if not re.fullmatch(r"[A-Za-z0-9._:+/#-]{1,80}", value):
            raise ValueError("target may only contain letters, numbers and . _ : + / # -")

        return value


class PromptResponse(BaseModel):
    video_id: str
    targets: list[str]
    prompts: dict[str, str]


class TokensResponse(BaseModel):
    video_id: str
    data: dict[str, Any]


class StatsSummaryResponse(BaseModel):
    total_videos: int
    duration_distribution: dict[str, int]
    hook_type_frequency: dict[str, int]
    avg_cuts_per_10s: float
    subtitle_density_distribution: dict[str, int]
    typical_beats: dict[str, Any]


class TopPatternItem(BaseModel):
    pattern: str
    count: int


class TopPatternsResponse(BaseModel):
    top_patterns: list[TopPatternItem]
