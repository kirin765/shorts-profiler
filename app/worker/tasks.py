from __future__ import annotations

import traceback
from datetime import datetime

from app.core.config import tmp_dir, videos_dir
from app.core.db import SessionLocal
from app.core.models import Job, Tokens, Video
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


def _build_tokens(
    video: Video,
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
    warnings: list[str],
) -> dict:
    duration = float(meta.get("duration_sec", 0.0) or 0.0)
    return {
        "schema_version": "1.0",
        "video_id": video.id,
        "duration_sec": round(duration, 4),
        "resolution": {"w": meta.get("width"), "h": meta.get("height")},
        "hook": {
            "time_range": [0.0, min(2.0, max(0.8, duration * 0.1))],
            "hook_text_ocr": None,
            "hook_type": _infer_hook_type(total_chars, density, cuts_per_10),
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
        },
        "notes": {
            "safety": "Abstracted patterns only. No creator-identifiable text or verbatim OCR text.",
            "limitations": warnings,
        },
        "status": "done",
    }


def _safe_bucket(v: float) -> str:
    if v <= 6:
        return "low"
    if v <= 14:
        return "mid"
    return "high"


def run_analysis(video_id: str, job_id: str) -> dict:
    db = SessionLocal()
    work_dir = tmp_dir() / job_id
    job = None

    try:
        _update_job(db, job_id, status="running", progress=3.0)

        video = db.query(Video).filter(Video.id == video_id).first()
        if video is None:
            raise ValueError(f"video {video_id} not found")

        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            raise ValueError(f"job {job_id} not found")

        source = videos_dir() / f"{video_id}.mp4"
        if not source.exists():
            raise ValueError(f"source video missing: {source}")

        warnings: list[str] = []
        meta = media.ffprobe_info(source)
        video.duration_sec = float(meta.get("duration_sec", 0.0) or 0.0)
        video.width = meta.get("width")
        video.height = meta.get("height")
        db.commit()
        duration = video.duration_sec or 0.0

        if duration <= 0:
            warnings.append("duration missing from ffprobe")

        _update_job(db, job_id, progress=10.0)
        all_frames = media.sample_frames(source, work_dir / "frames_all", interval_sec=0.5)
        first_frames = media.sample_frames(source, work_dir / "frames_first3", interval_sec=0.2, max_seconds=3.0)

        _update_job(db, job_id, progress=35.0)
        cuts = media.estimate_cuts(source, all_frames, duration)
        cut_count = len(cuts)
        avg_shot_len = (duration / cut_count) if cut_count > 0 else duration
        cuts_per_10 = (cut_count / duration) * 10.0 if duration > 0 else 0.0

        _update_job(db, job_id, progress=52.0)
        total_chars, position_ratio, subtitle_present, subtitles = media.extract_text_frames(all_frames)
        density = _safe_bucket(total_chars / max(duration, 1.0))
        position = "unknown"
        if position_ratio:
            position = max(position_ratio, key=position_ratio.get)

        _update_job(db, job_id, progress=62.0)
        face_ratio, closeup_ratio = media.estimate_face_presence(all_frames[:120])
        bg_complexity = media.estimate_background_complexity(all_frames)

        _update_job(db, job_id, progress=74.0)
        audio_wav = work_dir / "audio.wav"
        has_audio, bpm_est, energy_curve, silence_ratio = media.extract_audio_metrics(source, audio_wav)

        _update_job(db, job_id, progress=90.0)
        beats = media.build_beat_structure(duration, cuts_per_10)

        tokens_json = _build_tokens(
            video=video,
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
            warnings=warnings,
        )

        if subtitle_present and subtitles:
            # protect against accidental content leakage: only keep aggregate length
            if len(subtitles) > 120:
                warnings.append("ocr text count exceeded sample cap")
        elif not subtitle_present:
            tokens_json["notes"]["limitations"].append("subtitle likely absent or unreadable")

        # OCR failure should not fail the whole job
        if not all_frames:
            warnings.append("frame sampling returned no images")

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
        db.close()


if __name__ == "__main__":
    pass
