# shorts-profiler

Local-first FastAPI + Redis/RQ + Postgres MVP for short-form video analysis.

## 1) What this repo does

- Uploads videos by file or YouTube/TikTok URL
- Runs async analysis in RQ worker
- Stores structured tokens (schema v1.0), no raw OCR/creator-identifiable text
- Generates prompts only when requested
- Provides simple aggregated statistics APIs

## 2) Architecture

- `app/api`: FastAPI endpoints
- `app/core`: media util, settings, models, schemas, prompt builder
- `app/worker`: RQ worker tasks
- `migrations`: Alembic migration files
- `storage/videos`, `storage/tmp`: media and temporary work files

## 3) Requirements

- Python 3.11+
- Docker + Docker Compose
- ffmpeg
- tesseract-ocr
- yt-dlp
- Postgres, Redis (docker-compose will start both)

## 4) Environment variables

Example `.env`:

```env
APP_ENV=development
DATABASE_URL=postgresql+psycopg2://shorts:shorts@postgres:5432/shorts_profiler
REDIS_URL=redis://redis:6379/0
STORAGE_PATH=./storage
VIDEO_BUCKET_PATH=videos
TMP_PATH=tmp
QUEUE_NAME=shorts
CLEANUP_SOURCE_VIDEO=true
YT_DLP_ARGS=--format mp4 --no-check-certificate
```

- `CLEANUP_SOURCE_VIDEO=true` (default): delete original uploaded video after analysis
- `YT_DLP_ARGS`: optional args passed to yt-dlp

For local development without Docker, adjust `DATABASE_URL`/`REDIS_URL` to host endpoints.

## 5) Run (one command)

```bash
cp .env.example .env
docker compose up --build -d --scale worker=1
```

Then in another terminal:

```bash
docker compose exec api bash -lc "alembic upgrade head"
```

### One-command verify

```bash
curl http://127.0.0.1:8000/health
```

Expected: `{"status":"ok"}`.

## 6) API quick calls

All URLs are `http://127.0.0.1:8000`.

- Upload (file)
  - `POST /videos/upload` form-data: `file`, `category_tag?`

```bash
curl -X POST "http://127.0.0.1:8000/videos/upload" -F "file=@sample.mp4" -F "category_tag=review"
```

- Upload (URL, only YouTube/TikTok allowed)
  - `POST /videos/upload` form-data: `source_url`, `category_tag?`

```bash
curl -X POST "http://127.0.0.1:8000/videos/upload" -F "source_url=https://www.youtube.com/shorts/..." -F "category_tag=review"
```

- Start analyze

```bash
curl -X POST "http://127.0.0.1:8000/jobs/analyze" -H "Content-Type: application/json" -d "{\"video_id\":\"<VIDEO_ID>\"}"
```

- Job status

```bash
curl -X GET "http://127.0.0.1:8000/jobs/<JOB_ID>"
```

- Tokens

```bash
curl -X GET "http://127.0.0.1:8000/videos/<VIDEO_ID>/tokens"
```

- Prompt (model-specific)

```bash
curl -X POST "http://127.0.0.1:8000/videos/<VIDEO_ID>/prompt" \
  -H "Content-Type: application/json" \
  -d "{\"target\":\"sora\"}"

curl -X POST "http://127.0.0.1:8000/videos/<VIDEO_ID>/prompt" \
  -H "Content-Type: application/json" \
  -d "{\"target\":\"seedance\"}"

# custom model name supported
curl -X POST "http://127.0.0.1:8000/videos/<VIDEO_ID>/prompt" \
  -H "Content-Type: application/json" \
  -d "{\"target\":\"gpt-4o-mini\"}"

# all built-ins
curl -X POST "http://127.0.0.1:8000/videos/<VIDEO_ID>/prompt" \
  -H "Content-Type: application/json" \
  -d "{\"target\":\"all\"}"
```

- Stats

```bash
curl -X GET "http://127.0.0.1:8000/stats/summary?category_tag=review&duration_bucket=30-60"
curl -X GET "http://127.0.0.1:8000/stats/patterns/top?category_tag=review&limit=5"
```

## 7) Processing rules

- Queue: one worker only (`docker compose up --scale worker=1`) keeps FIFO execution.
- Analysis state transitions: `queued -> running -> done|failed`.
- Prompt is generated only via `POST /videos/{video_id}/prompt`.
- Token schema is fixed at `schema_version: 1.0`.
- `hook.hook_text_ocr` is saved in token payload for hook text hint.
- Source cleanup:
  - `CLEANUP_SOURCE_VIDEO=true` delete original mp4 after job finishes (pass/fail)
  - temporary artifacts (`frames_*`, `audio.wav`) are always deleted

## 8) URL upload compatibility

- Allowed hosts:
  - YouTube: `youtube.com`, `youtu.be`, `m.youtube.com`
  - TikTok: `tiktok.com`, `vm.tiktok.com`
- URL must be `http/https`.
- Failure to download returns 400.

## 9) Smoke test

```powershell
# install requirements first (for local tests, if needed)
# powershell: .\scripts\smoke-test.ps1 -VideoPath ".\storage\\sample.mp4"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke-test.ps1 -VideoPath "path\\to\\sample.mp4"
```

Optional URL tests:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke-test.ps1 -VideoPath ".\storage\\sample.mp4" -YoutubeUrl "https://www.youtube.com/shorts/..." -TikTokUrl "https://www.tiktok.com/@..."
```

## 10) Troubleshooting

- Health API fail: `docker compose logs -f api`
- Job stuck: check worker logs `docker compose logs -f worker`
- DB table mismatch: run `docker compose exec api bash -lc "alembic upgrade head"`
- FFmpeg/Tesseract/yt-dlp missing in container: ensure Dockerfile install step includes `ffmpeg`, `tesseract-ocr`
- Mount issues on Windows: use this compose file without host bind path for storage (named volume `storage`)

## 11) Branch & push flow

```bash
git checkout -b codex/shorts-profiler-mvp-YYYYMMDD
git add .
git commit -m "feat: implement shorts-profiler mvp"
git remote add origin https://github.com/kirin765/shorts-profiler.git
git push -u origin codex/shorts-profiler-mvp-YYYYMMDD
```
