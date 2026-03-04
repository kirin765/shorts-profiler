from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class Resolution(BaseModel):
    w: int | None = Field(default=None, ge=1)
    h: int | None = Field(default=None, ge=1)


class Hook(BaseModel):
    time_range: list[float]
    hook_text_ocr: str | None = Field(default=None, max_length=500)
    hook_type: str = "other"
    hook_spoken_summary: str | None = Field(default=None, max_length=300)


class Editing(BaseModel):
    cut_count: int
    avg_shot_len_sec: float
    cuts_per_10s: float
    zoom_events_est: int = 0


class Subtitle(BaseModel):
    present: bool
    position: str = "unknown"
    density: str = "low"
    style_tags: list[str] = Field(default_factory=list)
    chars_per_sec_est: float = 0.0


class Visual(BaseModel):
    face_presence_ratio_est: float = 0.0
    closeup_ratio_est: float = 0.0
    background_complexity: str = "unknown"


class Audio(BaseModel):
    has_audio: bool
    bpm_est: int = 0
    energy_curve: str = "unknown"
    silence_ratio_est: float = 0.0


class Notes(BaseModel):
    safety: str = "Abstracted patterns only. No creator-identifiable text or verbatim OCR text."
    limitations: list[str] = Field(default_factory=list)


class Shot(BaseModel):
    shot_id: int
    t0: float
    t1: float
    keyframes: list[float] = Field(default_factory=list)
    source: str = "hist"


class TextEventDerived(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    has_number: bool = False
    text_type: str = "unknown"
    char_len_est: int = 0
    density_est: str = "low"


class TextEvent(BaseModel):
    t0: float
    t1: float
    role: str
    position: str = "middle"
    size_est: float = 0.0
    style_tags: list[str] = Field(default_factory=list)
    derived: TextEventDerived


class Structure(BaseModel):
    beats: list[dict[str, Any]] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)


class TokensSchemaV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    video_id: str
    duration_sec: float
    resolution: Resolution
    hook: Hook
    editing: Editing
    subtitle: Subtitle
    visual: Visual
    audio: Audio
    structure: Structure
    notes: Notes
    status: str
    text_events: list[TextEvent] | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != "1.0":
            raise ValueError("schema_version must be 1.0")
        return value


def validate_tokens(payload: dict[str, Any]) -> TokensSchemaV1:
    return TokensSchemaV1.model_validate(payload)


__all__ = ["TokensSchemaV1", "ValidationError", "validate_tokens"]
