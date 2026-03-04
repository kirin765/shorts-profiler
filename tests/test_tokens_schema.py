from pydantic import ValidationError

from app.core.token_schemas import TokensSchemaV1


def _base_tokens() -> dict:
    return {
        "schema_version": "1.0",
        "video_id": "vid123",
        "duration_sec": 8.4,
        "resolution": {"w": 1080, "h": 1920},
        "hook": {
            "time_range": [0.0, 0.8],
            "hook_text_ocr": "sample text",
            "hook_type": "promise",
            "hook_spoken_summary": None,
        },
        "editing": {
            "cut_count": 2,
            "avg_shot_len_sec": 1.5,
            "cuts_per_10s": 2.3,
            "zoom_events_est": 0,
        },
        "subtitle": {
            "present": True,
            "position": "bottom",
            "density": "low",
            "style_tags": ["bold_est"],
            "chars_per_sec_est": 10.2,
        },
        "visual": {
            "face_presence_ratio_est": 0.4,
            "closeup_ratio_est": 0.2,
            "background_complexity": "mid",
        },
        "audio": {"has_audio": True, "bpm_est": 120, "energy_curve": "mid", "silence_ratio_est": 0.1},
        "structure": {
            "beats": [],
            "shots": [],
        },
        "notes": {"safety": "ok", "limitations": []},
        "status": "done",
        "text_events": [],
    }


def test_tokens_schema_validates_required_fields() -> None:
    payload = _base_tokens()
    validated = TokensSchemaV1.model_validate(payload)
    assert validated.video_id == "vid123"
    assert validated.schema_version == "1.0"


def test_hook_text_limit() -> None:
    payload = _base_tokens()
    payload["hook"]["hook_text_ocr"] = "x" * 600
    try:
        TokensSchemaV1.model_validate(payload)
    except ValidationError as exc:
        assert exc.errors()
    else:
        raise AssertionError("expected validation error for long hook_text_ocr")
