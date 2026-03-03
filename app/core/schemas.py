from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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


class PromptTarget(str, Enum):
    sora = "sora"
    seedance = "seedance"
    script = "script"
    all = "all"


class PromptRequest(BaseModel):
    target: PromptTarget = PromptTarget.all


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
