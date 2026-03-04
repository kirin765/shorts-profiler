from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from app.analysis.asr import generate_speech_segments
from app.analysis.shots import build_shots
from app.analysis.text_events import (
    build_position_stats,
    build_text_events,
    summarize_hook_from_events,
)
from app.core.config import settings, tmp_dir, videos_dir
from app.core.db import SessionLocal
from app.core.models import Job, Tokens, Video
from app.core.token_schemas import TokensSchemaV1
from app.core import media


def _update_job(db, job_id: str, status: str | None = None, progress: float | None = None, error: str | None = None):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return

    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = max(0.0, min(100.0, float(progress)))
    if error is not None:
        job.error = error

    if status == "running" and job.started_at is None:
        job.started_at = datetime.utcnow()
    if status in {"done", "failed"} and job.finished_at is None:
        job.finished_at = datetime.utcnow()

    db.commit()


def _infer_hook_type(total_chars: int, density: str, cuts_per_10: float) -> str:
    if cuts_per_10 >= 6:
        return "mistake_fix"
    if total_chars > 140 and density in {"high", "mid"}:
        return "listicle"
    if cuts_per_10 <= 2:
        return "promise"
    if density == "high":
        return "question"
    return "other"


def _safe_bucket(v: float) -> str:
    if v <= 6:
        return "low"
    if v <= 14:
        return "mid"
    return "high"


def _sample_frame_timestamps(frames: list[Path], interval_sec: float) -> list[tuple[Path, float]]:
    return [(frame, i * interval_sec) for i, frame in enumerate(frames)]


def _build_tokens(
    video_id: str,
    meta: dict,
    cuts_count: int,
    cuts_per_10: float,
    avg_shot_len: float,
    total_chars: int,
    density: str,
    position: str,
    face_ratio: float,
    closeup_ratio: float,
    bg_complexity: str,
    has_audio: bool,
    bpm_est: int,
    energy_curve: str,
    silence_ratio: float,
    beats: list,
    shots: list[dict],
    text_events: list[dict],
    hook_text_ocr: str | None,
    hook_spoken_summary: str | None,
    warnings: list[str],
    extensions: dict,
) -> dict:
    duration = float(meta.get("duration_sec", 0.0) or 0.0)
    resolution = {"w": meta.get("width"), "h": meta.get("height")}

    token_payload: dict = {
        "schema_version": "1.0",
        "video_id": video_id,
        "duration_sec": round(duration, 4),
        "resolution": resolution,
        "hook": {
            "time_range": [0.0, min(2.0, max(0.8, duration * 0.1))],
            "hook_text_ocr": hook_text_ocr,
            "hook_type": _infer_hook_type(total_chars, density, cuts_per_10),
            "hook_spoken_summary": hook_spoken_summary,
        },
        "editing": {
            "cut_count": cuts_count,
            "avg_shot_len_sec": round(avg_shot_len, 4),
            "cuts_per_10s": round(cuts_per_10, 3),
            "zoom_events_est": int(max(0, round(cuts_count * 0.18))),
        },
        "subtitle": {
            "present": total_chars > 0,
            "position": position,
            "density": density,
            "style_tags": ["bold_est", "outline_est"],
            "chars_per_sec_est": round(total_chars / max(duration, 1.0), 3),
        },
        "visual": {
            "face_presence_ratio_est": round(face_ratio, 3),
            "closeup_ratio_est": round(closeup_ratio, 3),
            "background_complexity": bg_complexity,
        },
        "audio": {
            "has_audio": bool(has_audio),
            "bpm_est": int(bpm_est),
            "energy_curve": energy_curve,
            "silence_ratio_est": round(silence_ratio, 3),
        },
        "structure": {
            "beats": beats,
            "shots": shots,
        },
        "notes": {
            "safety": "Abstracted patterns only. No creator-identifiable text or verbatim OCR text.",
            "limitations": warnings,
        },
        "text_events": text_events,
        "extensions": extensions,
        "status": "done",
    }

    if not shots and not warnings:
        token_payload["notes"]["limitations"].append("shots empty: fallback detection produced no boundaries")

    return token_payload


def _normalize_for_schema(payload: dict, warnings: list[str]) -> dict:
    payload.setdefault("extensions", {})
    hook = payload.get("hook", {})
    if isinstance(hook, dict):
        hook_text = hook.get("hook_text_ocr")
        if isinstance(hook_text, str) and len(hook_text) > 500:
            hook["hook_text_ocr"] = hook_text[:500]
            warnings.append("hook_text_ocr clipped to 500 chars")
        hook_spoken = hook.get("hook_spoken_summary")
        if isinstance(hook_spoken, str) and len(hook_spoken) > 300:
            hook["hook_spoken_summary"] = hook_spoken[:300]
            warnings.append("hook_spoken_summary clipped to 300 chars")

    # ensure common required keys exist to keep compatibility
    payload.setdefault("schema_version", "1.0")
    payload.setdefault("resolution", {"w": None, "h": None})
    payload.setdefault("audio", {}).setdefault("has_audio", False)
    payload.setdefault("status", "done")
    payload.setdefault("notes", {}).setdefault("safety", "Abstracted patterns only. No creator-identifiable text or verbatim OCR text.")
    payload.setdefault("notes", {}).setdefault("limitations", [])
    payload["notes"]["limitations"] = list(payload["notes"].get("limitations", []))

    return payload


def _validate_payload_or_raise(payload: dict, warnings: list[str]) -> dict:
    normalized = _normalize_for_schema(payload, warnings)
    try:
        TokensSchemaV1.model_validate(normalized)
        return normalized
    except ValidationError as exc:
        fail_critical = []

        for issue in exc.errors():
            if issue.get("type", "") in {"string_too_long", "string_too_short"}:
                continue
            fail_critical.append(issue)

        if not fail_critical:
            _normalize_for_schema(normalized, warnings)
            try:
                TokensSchemaV1.model_validate(normalized)
                return normalized
            except ValidationError as second_exc:
                fail_critical = second_exc.errors()

        if fail_critical:
            raise RuntimeError(f"token schema validation failed: {exc}") from exc

    return normalized


def run_analysis(video_id: str, job_id: str) -> dict:
    db = SessionLocal()
    work_dir = tmp_dir() / job_id
    source_path = videos_dir() / f"{video_id}.mp4"
    job = None

    try:
        _update_job(db, job_id, status="running", progress=3.0)

        video = db.query(Video).filter(Video.id == video_id).first()
        if video is None:
            raise ValueError(f"video {video_id} not found")

        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            raise ValueError(f"job {job_id} not found")

        if not source_path.exists():
            raise ValueError(f"source video missing: {source_path}")

        warnings: list[str] = []
        meta = media.ffprobe_info(source_path)
        video.duration_sec = float(meta.get("duration_sec", 0.0) or 0.0)
        video.width = meta.get("width")
        video.height = meta.get("height")
        db.commit()
        duration = video.duration_sec or 0.0
        if duration <= 0:
            warnings.append("duration missing from ffprobe")

        _update_job(db, job_id, progress=10.0)
        shots = build_shots(source_path, duration_sec=duration, frame_fallback_interval=0.5)
        shot_times: list[float] = []
        for shot in shots:
            shot_times.extend([float(t) for t in shot.get("keyframes", [])])

        # dedupe keyframes, clamp to video duration
        shot_times = sorted({_ for _ in shot_times if 0 <= _ <= duration})
        shot_frames = media.sample_frames_at_timestamps(source_path, work_dir / "frames_shots", shot_times)

        # first3 hook-focused dense scan; early-stop based on detected text-events (best effort)
        first3_candidates = media.sample_frames(
            source_path,
            work_dir / "frames_first3",
            interval_sec=0.2,
            max_seconds=min(3.0, duration if duration > 0 else 3.0),
        )
        first3_with_time = _sample_frame_timestamps(first3_candidates, 0.2)
        raw_first3 = media.extract_text_events_from_frames(first3_with_time)
        if not raw_first3 and duration > 0:
            warnings.append("first3-seconds dense sampling returned no text detections")
        else:
            first3_events = build_text_events(raw_first3, duration_sec=duration)
            if sum(1 for e in first3_events if e["t1"] - e["t0"] > 0) >= 3:
                warnings.append("early-exit triggered: first3 text-events reached threshold")

        _update_job(db, job_id, progress=35.0)
        raw_shot_events = media.extract_text_events_from_frames(shot_frames)

        all_raw_events = raw_shot_events + raw_first3
        text_events = build_text_events(all_raw_events, duration_sec=duration)

        position_map, subtitle_present, total_chars, _ = build_position_stats(text_events)
        if not text_events:
            warnings.append("text-events skipped: OCR unreadable or too sparse")

        hook_text_ocr = summarize_hook_from_events(text_events)

        if subtitle_present:
            position = max(position_map, key=position_map.get)
        else:
            position = "unknown"

        density = _safe_bucket(total_chars / max(duration, 1.0))

        _update_job(db, job_id, progress=52.0)
        all_visual_refs = [frame for frame, _ in shot_frames]
        if not all_visual_refs:
            all_visual_refs = first3_candidates
        face_ratio, closeup_ratio = media.estimate_face_presence(all_visual_refs[:120])
        bg_complexity = media.estimate_background_complexity(all_visual_refs)

        _update_job(db, job_id, progress=74.0)
        audio_wav = work_dir / "audio.wav"
        has_audio, bpm_est, energy_curve, silence_ratio = media.extract_audio_metrics(source_path, audio_wav)

        _update_job(db, job_id, progress=82.0)
        asr_output = generate_speech_segments(audio_wav, enable_asr=settings.enable_asr and bool(has_audio))
        warnings.extend(asr_output.get("warnings", []))
        extensions = asr_output.get("extensions", {})
        hook_spoken_summary = asr_output.get("hook_spoken_summary")

        _update_job(db, job_id, progress=90.0)
        cut_count = max(0, len(shots) - 1)
        avg_shot_len = (duration / cut_count) if cut_count > 0 else duration
        cuts_per_10 = (cut_count / duration) * 10.0 if duration > 0 else 0.0
        beats = media.build_beat_structure(duration, cuts_per_10)

        tokens_json = _build_tokens(
            video_id=video_id,
            meta=meta,
            cuts_count=cut_count,
            cuts_per_10=cuts_per_10,
            avg_shot_len=avg_shot_len,
            total_chars=total_chars,
            density=density,
            position=position,
            face_ratio=face_ratio,
            closeup_ratio=closeup_ratio,
            bg_complexity=bg_complexity,
            has_audio=has_audio,
            bpm_est=bpm_est,
            energy_curve=energy_curve,
            silence_ratio=silence_ratio,
            beats=beats,
            shots=shots,
            text_events=text_events,
            hook_text_ocr=hook_text_ocr,
            hook_spoken_summary=hook_spoken_summary,
            warnings=warnings,
            extensions=extensions,
        )

        tokens_json = _validate_payload_or_raise(tokens_json, warnings)

        if not shot_frames:
            warnings.append("shot keyframe extraction empty; fallback-only metrics used")

        if not subtitle_present:
            warnings.append("subtitle likely absent or unreadable")

        token_row = db.query(Tokens).filter(Tokens.video_id == video_id).first()
        if token_row:
            token_row.schema_version = "1.0"
            token_row.tokens_json = tokens_json
            token_row.created_at = datetime.utcnow()
        else:
            db.add(Tokens(video_id=video_id, schema_version="1.0", tokens_json=tokens_json))

        _update_job(db, job_id, status="done", progress=100.0)
        db.commit()
        return tokens_json

    except Exception as exc:
        if job is not None:
            _update_job(
                db,
                job_id,
                status="failed",
                progress=100.0,
                error=f"analysis failed: {exc}\n{traceback.format_exc()}",
            )
        else:
            db.rollback()
        raise
    finally:
        try:
            media.cleanup_dir(work_dir)
        except Exception:
            pass
        if settings.cleanup_source_video and source_path.exists():
            try:
                source_path.unlink()
            except Exception:
                pass
        db.close()


if __name__ == "__main__":
    pass
