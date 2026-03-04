from __future__ import annotations

import csv
import io
import json
import time
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from redis import Redis
from rq import Queue
from sqlalchemy.orm import Session

from app.core import media
from app.core.config import settings, videos_dir
from app.core.db import SessionLocal, get_db
from app.core.models import Job, JobLog, Prompt, Tokens, Video
from app.core.prompt_builder import build_prompts
from app.core.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    JobListItem,
    JobListResponse,
    JobLogItem,
    JobLogsResponse,
    JobStatusResponse,
    PromptItem,
    PromptRequest,
    PromptResponse,
    StatsSummaryResponse,
    TopPatternItem,
    TopPatternsResponse,
    TokensResponse,
    UploadCsvItem,
    UploadCsvResponse,
    UploadResponse,
    VideoPromptsResponse,
)

app = FastAPI(title="shorts-profiler", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    videos_dir().mkdir(parents=True, exist_ok=True)


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


def _is_supported_source_url(source_url: str) -> bool:
    try:
        parsed = urlparse(source_url)
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").lower()
    if not host:
        return False

    allow_hosts = (
        "youtube.com",
        "youtu.be",
        "m.youtube.com",
        "tiktok.com",
        "www.tiktok.com",
        "vm.tiktok.com",
    )
    return any(host == h or host.endswith("." + h) for h in allow_hosts)


def _read_json_body(db: Session, video_id: str) -> dict:
    token_row = db.query(Tokens).filter(Tokens.video_id == video_id).first()
    if token_row is None:
        raise HTTPException(status_code=404, detail="tokens not found. analyze job may be pending")
    return token_row.tokens_json


def _safe_trim(value: str | None) -> str:
    return (value or "").strip()


def _append_job_log(
    db: Session,
    job_id: str,
    step: str,
    message: str,
    level: str = "info",
    metadata: dict | None = None,
) -> None:
    db.add(
        JobLog(
            job_id=job_id,
            level=level,
            step=step,
            message=message,
            meta=metadata,
            created_at=datetime.utcnow(),
        )
    )


def _enqueue_analysis(db: Session, video_id: str) -> str:
    job_id = str(uuid.uuid4())
    job = Job(id=job_id, video_id=video_id, status="queued", progress=0.0)
    db.add(job)
    db.flush()

    _append_job_log(
        db,
        job_id,
        "enqueue",
        "analysis job queued",
        "info",
        {"video_id": video_id},
    )

    q = _queue()
    q.enqueue("app.worker.tasks.run_analysis", args=(video_id, job_id), job_id=job_id)

    _append_job_log(
        db,
        job_id,
        "enqueue",
        "analysis job sent to RQ",
        "info",
        {"queue": settings.queue_name},
    )
    db.commit()
    return job_id


def _parse_csv_url(row: dict) -> str:
    for key in ("source_url", "url", "link"):
        if key in row:
            value = _safe_trim(str(row.get(key)))
            if value:
                return value
    return ""


def _parse_csv_category(row: dict, default_category_tag: str) -> str:
    category = row.get("category_tag", "")
    if not _safe_trim(str(category)):
        category = row.get("category", "")
    return _safe_trim(str(category) or default_category_tag)


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
    source_url = _safe_trim(source_url)

    if file is not None:
        _validate_extension(file.filename or "")
        with target_path.open("wb") as out:
            import shutil

            shutil.copyfileobj(file.file, out)
        source_type = "file"
        source_ref = file.filename
    else:
        if not _is_supported_source_url(source_url):
            raise HTTPException(
                status_code=400,
                detail="source_url must be http/https and host youtube/tiktok domain",
            )
        try:
            downloaded = media.download_video_from_url_with_ytdlp(source_url, target_path)
            if downloaded != target_path:
                import shutil

                shutil.move(str(downloaded), str(target_path))
            source_type = "url"
            source_ref = source_url
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"download failed: {exc}") from exc

    video = Video(
        id=video_id,
        filename=target_path.name,
        category_tag=_safe_trim(category_tag),
        source_type=source_type,
        source_ref=source_ref,
    )
    db.add(video)
    db.commit()
    return UploadResponse(video_id=video_id)


@app.post("/videos/upload-csv", response_model=UploadCsvResponse)
def upload_csv(
    csv_file: UploadFile = File(...),
    default_category_tag: str = Form(default="batch"),
    auto_analyze: bool = Form(default=True),
    max_rows: int = Form(default=1000),
    db: Session = Depends(get_db),
):
    if max_rows <= 0:
        raise HTTPException(status_code=400, detail="max_rows must be greater than 0")

    batch_id = str(uuid.uuid4())
    accepted_rows = 0
    invalid_rows = 0
    items: list[UploadCsvItem] = []

    raw = csv_file.file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="invalid csv format: no header")

    for row_index, row in enumerate(reader, start=1):
        if row_index > max_rows:
            invalid_rows += 1
            items.append(
                UploadCsvItem(
                    row_index=row_index,
                    source_url=_parse_csv_url(row),
                    category_tag=_parse_csv_category(row, default_category_tag),
                    status="failed",
                    error="max_rows exceeded",
                )
            )
            continue

        source_url = _parse_csv_url(row)
        category_tag = _parse_csv_category(row, default_category_tag)

        if not source_url:
            invalid_rows += 1
            items.append(
                UploadCsvItem(
                    row_index=row_index,
                    source_url=source_url,
                    category_tag=category_tag,
                    status="failed",
                    error="missing source_url",
                )
            )
            continue

        if not _is_supported_source_url(source_url):
            invalid_rows += 1
            items.append(
                UploadCsvItem(
                    row_index=row_index,
                    source_url=source_url,
                    category_tag=category_tag,
                    status="failed",
                    error="unsupported source host",
                )
            )
            continue

        video_id = str(uuid.uuid4())
        target_path = videos_dir() / f"{video_id}.mp4"

        try:
            downloaded = media.download_video_from_url_with_ytdlp(source_url, target_path)
            if downloaded != target_path:
                import shutil

                shutil.move(str(downloaded), str(target_path))

            db.add(
                Video(
                    id=video_id,
                    filename=target_path.name,
                    category_tag=category_tag,
                    source_type="url",
                    source_ref=source_url,
                )
            )
            db.commit()

            status = "uploaded"
            job_id = None
            error_message: str | None = None

            if auto_analyze:
                try:
                    job_id = _enqueue_analysis(db, video_id)
                    status = "queued"
                except Exception as exc:
                    db.rollback()
                    status = "failed"
                    error_message = f"enqueue failed: {exc}"

            accepted_rows += 1
            items.append(
                UploadCsvItem(
                    row_index=row_index,
                    source_url=source_url,
                    video_id=video_id,
                    job_id=job_id,
                    status=status,
                    category_tag=category_tag,
                    error=error_message,
                )
            )
        except Exception as exc:
            db.rollback()
            invalid_rows += 1
            items.append(
                UploadCsvItem(
                    row_index=row_index,
                    source_url=source_url,
                    category_tag=category_tag,
                    status="failed",
                    error=str(exc),
                )
            )

    return UploadCsvResponse(
        batch_id=batch_id,
        accepted_rows=accepted_rows,
        invalid_rows=invalid_rows,
        items=items,
    )


@app.post("/jobs/analyze", response_model=AnalyzeResponse)
def start_analyze(payload: AnalyzeRequest, db: Session = Depends(get_db)):
    if not db.query(Video).filter(Video.id == payload.video_id).first():
        raise HTTPException(status_code=404, detail="video not found")

    try:
        job_id = _enqueue_analysis(db, payload.video_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to enqueue analysis: {exc}") from exc

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


@app.get("/jobs", response_model=JobListResponse)
def list_jobs(
    status: str | None = Query(default=None),
    video_id: str | None = Query(default=None),
    category_tag: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Job, Video.category_tag).join(Video, Video.id == Job.video_id)

    if status:
        query = query.filter(Job.status == status)
    if video_id:
        query = query.filter(Job.video_id == video_id)
    if category_tag:
        query = query.filter(Video.category_tag == category_tag)

    total = query.count()
    rows = (
        query.order_by(Job.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items: list[JobListItem] = [
        JobListItem(
            job_id=job.id,
            video_id=job.video_id,
            status=job.status,
            progress=float(job.progress or 0.0),
            error=job.error,
            created_at=(job.created_at or datetime.utcnow()).isoformat(),
            updated_at=(job.updated_at.isoformat() if job.updated_at else None),
            category_tag=job_category,
        )
        for job, job_category in rows
    ]

    return JobListResponse(items=items, total=total, limit=limit, offset=offset)


@app.get("/jobs/{job_id}/logs", response_model=JobLogsResponse)
def list_job_logs(
    job_id: str,
    since_id: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    query = db.query(JobLog).filter(JobLog.job_id == job_id)
    if since_id is not None:
        query = query.filter(JobLog.id > since_id)

    logs = query.order_by(JobLog.id.asc()).limit(limit).all()
    next_id = logs[-1].id if logs else None

    return JobLogsResponse(
        job_id=job_id,
        logs=[
            JobLogItem(
                id=log.id,
                job_id=log.job_id,
                level=log.level,
                step=log.step,
                message=log.message,
                metadata=log.meta,
                created_at=(log.created_at or datetime.utcnow()).isoformat(),
            )
            for log in logs
        ],
        next_id=next_id,
    )


@app.get("/jobs/{job_id}/stream")
def stream_job(job_id: str, since_id: int | None = Query(default=None, ge=0)):
    if since_id is None:
        since_id = 0

    def event_generator():
        last_log_id = int(since_id)
        last_status: tuple[str, float] | None = None

        while True:
            with SessionLocal() as db:
                job = db.query(Job).filter(Job.id == job_id).first()
                if not job:
                    yield _sse("error", {"job_id": job_id, "message": "job not found"})
                    return

                logs = (
                    db.query(JobLog)
                    .filter(JobLog.job_id == job_id, JobLog.id > last_log_id)
                    .order_by(JobLog.id.asc())
                    .limit(200)
                    .all()
                )
                for log in logs:
                    last_log_id = log.id
                    yield _sse(
                        "log",
                        {
                            "id": log.id,
                            "job_id": log.job_id,
                            "level": log.level,
                            "step": log.step,
                            "message": log.message,
                            "metadata": log.meta,
                            "created_at": (log.created_at or datetime.utcnow()).isoformat(),
                        },
                    )

                status_payload = {
                    "job_id": job.id,
                    "status": job.status,
                    "progress": float(job.progress or 0.0),
                    "error": job.error,
                    "updated_at": (job.updated_at or job.created_at or datetime.utcnow()).isoformat(),
                }

                status_key = (job.status, round(float(job.progress or 0.0), 3))
                if last_status != status_key:
                    last_status = status_key
                    yield _sse("status", status_payload)

                if job.status in {"done", "failed"}:
                    return

            yield _sse("heartbeat", {"alive": True})
            time.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/videos/{video_id}/tokens", response_model=TokensResponse)
def get_tokens(video_id: str, db: Session = Depends(get_db)):
    if not db.query(Video).filter(Video.id == video_id).first():
        raise HTTPException(status_code=404, detail="video not found")
    data = _read_json_body(db, video_id)
    return TokensResponse(video_id=video_id, data=data)


@app.post("/videos/{video_id}/prompt", response_model=PromptResponse)
def build_prompt(video_id: str, payload: PromptRequest, db: Session = Depends(get_db)):
    tokens = _read_json_body(db, video_id)
    built = build_prompts(tokens, payload.target)

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


@app.get("/videos/{video_id}/prompts", response_model=VideoPromptsResponse)
def get_video_prompts(video_id: str, db: Session = Depends(get_db)):
    if not db.query(Video).filter(Video.id == video_id).first():
        raise HTTPException(status_code=404, detail="video not found")

    rows = (
        db.query(Prompt)
        .filter(Prompt.video_id == video_id)
        .order_by(Prompt.created_at.desc(), Prompt.id.desc())
        .all()
    )

    return VideoPromptsResponse(
        video_id=video_id,
        prompts=[
            PromptItem(
                id=r.id,
                target=r.target,
                prompt_text=r.prompt_text,
                created_at=(r.created_at or datetime.utcnow()).isoformat(),
            )
            for r in rows
        ],
    )


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
