from __future__ import annotations

from datetime import datetime
import shutil
import uuid
from typing import Optional

import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from redis import Redis
from rq import Queue
from sqlalchemy.orm import Session

from app.core.config import settings, videos_dir, tmp_dir
from app.core.db import get_db
from app.core.models import Job, Prompt, Tokens, Video
from app.core.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    JobStatusResponse,
    PromptRequest,
    PromptResponse,
    StatsSummaryResponse,
    TopPatternItem,
    TopPatternsResponse,
    TokensResponse,
    UploadResponse,
)
from app.core.prompt_builder import build_prompts


app = FastAPI(title="shorts-profiler", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    videos_dir().mkdir(parents=True, exist_ok=True)
    tmp_dir().mkdir(parents=True, exist_ok=True)


app.mount("/static", StaticFiles(directory="app/api/static", html=True), name="static")


@app.get("/")
def index():
    return FileResponse("app/api/static/index.html")


def _queue() -> Queue:
    redis_conn = Redis.from_url(settings.redis_url)
    return Queue(settings.queue_name, connection=redis_conn)


def _coerce_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid date: {value}") from exc


def _bucket_duration(seconds: float | None) -> str:
    seconds = float(seconds or 0.0)
    if seconds <= 15:
        return "<=15"
    if seconds <= 30:
        return "15-30"
    if seconds <= 60:
        return "30-60"
    return "60+"


def _validate_extension(filename: str) -> None:
    allowed = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    if not any(filename.lower().endswith(ext) for ext in allowed):
        raise HTTPException(status_code=400, detail=f"unsupported format. allowed: {', '.join(sorted(allowed))}")


def _read_json_body(db: Session, video_id: str) -> dict:
    token_row = db.query(Tokens).filter(Tokens.video_id == video_id).first()
    if token_row is None:
        raise HTTPException(status_code=404, detail="tokens not found. analyze job may be pending")
    return token_row.tokens_json


@app.post("/videos/upload", response_model=UploadResponse)
def upload_video(
    file: UploadFile | None = File(default=None),
    category_tag: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    if file is None and not source_url:
        raise HTTPException(status_code=400, detail="file or source_url required")

    video_id = str(uuid.uuid4())
    target_path = videos_dir() / f"{video_id}.mp4"

    if file is not None:
        _validate_extension(file.filename or "")
        with target_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        source_type = "file"
        source_ref = file.filename
    else:
        if not source_url.startswith("http://") and not source_url.startswith("https://"):
            raise HTTPException(status_code=400, detail="source_url must start with http/https")
        try:
            response = requests.get(source_url, timeout=60, stream=True)
            response.raise_for_status()
            with target_path.open("wb") as out:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        out.write(chunk)
            source_type = "url"
            source_ref = source_url
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"download failed: {exc}") from exc

    video = Video(
        id=video_id,
        filename=target_path.name,
        category_tag=category_tag,
        source_type=source_type,
        source_ref=source_ref,
    )
    db.add(video)
    db.commit()
    return UploadResponse(video_id=video_id)


@app.post("/jobs/analyze", response_model=AnalyzeResponse)
def start_analyze(payload: AnalyzeRequest, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == payload.video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="video not found")

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, video_id=payload.video_id, status="queued", progress=0)
    db.add(job)
    db.commit()

    q = _queue()
    try:
        q.enqueue("app.worker.tasks.run_analysis", args=(payload.video_id, job_id), job_id=job_id)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.finished_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=500, detail="failed to enqueue analysis") from exc

    return AnalyzeResponse(job_id=job_id)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatusResponse(
        job_id=job.id,
        video_id=job.video_id,
        status=job.status,
        progress=float(job.progress or 0.0),
        error=job.error,
    )


@app.get("/videos/{video_id}/tokens", response_model=TokensResponse)
def get_tokens(video_id: str, db: Session = Depends(get_db)):
    if not db.query(Video).filter(Video.id == video_id).first():
        raise HTTPException(status_code=404, detail="video not found")
    data = _read_json_body(db, video_id)
    return TokensResponse(video_id=video_id, data=data)


@app.post("/videos/{video_id}/prompt", response_model=PromptResponse)
def build_prompt(video_id: str, payload: PromptRequest, db: Session = Depends(get_db)):
    tokens = _read_json_body(db, video_id)
    built = build_prompts(tokens, payload.target.value)

    for target, text in built.items():
        row = (
            db.query(Prompt)
            .filter(Prompt.video_id == video_id, Prompt.target == target)
            .first()
        )
        if row:
            row.prompt_text = text
            row.created_at = datetime.utcnow()
        else:
            db.add(Prompt(video_id=video_id, target=target, prompt_text=text))

    db.commit()

    return PromptResponse(video_id=video_id, targets=list(built.keys()), prompts=built)


@app.get("/stats/summary", response_model=StatsSummaryResponse)
def stats_summary(
    category_tag: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    duration_bucket: str | None = None,
    db: Session = Depends(get_db),
):
    start_dt = _coerce_date(start_date)
    end_dt = _coerce_date(end_date)
    if start_dt and end_dt and end_dt < start_dt:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    query = db.query(Video, Tokens).join(Tokens, Tokens.video_id == Video.id)
    if category_tag:
        query = query.filter(Video.category_tag == category_tag)
    if start_dt:
        query = query.filter(Video.created_at >= start_dt)
    if end_dt:
        query = query.filter(Video.created_at <= end_dt)

    rows = query.all()

    def maybe_filter(bucket: str, row_tuple):
        if not bucket:
            return True
        duration = row_tuple[0].duration_sec
        return _bucket_duration(duration) == bucket

    rows = [r for r in rows if maybe_filter(duration_bucket, r)]

    if not rows:
        return StatsSummaryResponse(
            total_videos=0,
            duration_distribution={"<=15": 0, "15-30": 0, "30-60": 0, "60+": 0},
            hook_type_frequency={},
            avg_cuts_per_10s=0.0,
            subtitle_density_distribution={"low": 0, "mid": 0, "high": 0},
            typical_beats={"median_hook_sec": 0.0, "median_cta_start_sec": 0.0},
        )

    duration_distribution = {"<=15": 0, "15-30": 0, "30-60": 0, "60+": 0}
    hook_type_frequency: dict[str, int] = {}
    subtitle_density_distribution = {"low": 0, "mid": 0, "high": 0}
    cuts_per_10s: list[float] = []
    hook_sec_list: list[float] = []
    cta_start_list: list[float] = []

    for video, token_row in rows:
        bucket = _bucket_duration(video.duration_sec)
        duration_distribution[bucket] = duration_distribution.get(bucket, 0) + 1

        tokens = token_row.tokens_json or {}
        hook = tokens.get("hook", {})
        editing = tokens.get("editing", {})
        subtitle = tokens.get("subtitle", {})
        structure = tokens.get("structure", {})

        hook_type = str(hook.get("hook_type", "other"))
        hook_type_frequency[hook_type] = hook_type_frequency.get(hook_type, 0) + 1

        density = str(subtitle.get("density", "low"))
        subtitle_density_distribution[density] = subtitle_density_distribution.get(density, 0) + 1

        cuts = float(editing.get("cuts_per_10s", 0.0) or 0.0)
        cuts_per_10s.append(cuts)

        beats = structure.get("beats", [])
        if isinstance(beats, list) and len(beats) >= 1:
            first = beats[0].get("t", [0, 0])[1]
            hook_sec_list.append(float(first))
        if isinstance(beats, list) and len(beats) >= 4:
            cta_start_list.append(float(beats[-1].get("t", [0, 0])[0]))

    avg_cut_10 = sum(cuts_per_10s) / max(len(cuts_per_10s), 1)
    import statistics

    typical = {
        "median_hook_sec": statistics.median(hook_sec_list) if hook_sec_list else 0.0,
        "median_cta_start_sec": statistics.median(cta_start_list) if cta_start_list else 0.0,
    }

    return StatsSummaryResponse(
        total_videos=len(rows),
        duration_distribution=duration_distribution,
        hook_type_frequency=hook_type_frequency,
        avg_cuts_per_10s=round(avg_cut_10, 3),
        subtitle_density_distribution=subtitle_density_distribution,
        typical_beats=typical,
    )


@app.get("/stats/patterns/top", response_model=TopPatternsResponse)
def top_patterns(
    category_tag: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    duration_bucket: str | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    start_dt = _coerce_date(start_date)
    end_dt = _coerce_date(end_date)
    if start_dt and end_dt and end_dt < start_dt:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    query = db.query(Video, Tokens).join(Tokens, Tokens.video_id == Video.id)
    if category_tag:
        query = query.filter(Video.category_tag == category_tag)
    if start_dt:
        query = query.filter(Video.created_at >= start_dt)
    if end_dt:
        query = query.filter(Video.created_at <= end_dt)
    rows = query.all()

    if duration_bucket:
        rows = [row for row in rows if _bucket_duration(row[0].duration_sec) == duration_bucket]
    patterns: dict[str, int] = {}
    for video, token_row in rows:
        t = token_row.tokens_json or {}
        hook_type = str((t.get("hook", {}) or {}).get("hook_type", "other"))
        cuts_per_10 = float((t.get("editing", {}) or {}).get("cuts_per_10s", 0.0) or 0.0)
        subtitle_density = str((t.get("subtitle", {}) or {}).get("density", "low"))
        bucket = "low"
        if cuts_per_10 >= 6:
            bucket = "high_cut"
        elif cuts_per_10 >= 3:
            bucket = "mid_cut"
        else:
            bucket = "low_cut"

        key = f"{hook_type}|{bucket}|{subtitle_density}"
        patterns[key] = patterns.get(key, 0) + 1

    sorted_patterns = sorted(patterns.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return TopPatternsResponse(
        top_patterns=[TopPatternItem(pattern=k, count=v) for k, v in sorted_patterns]
    )


@app.get("/health")
def health():
    return JSONResponse({"status": "ok"})
