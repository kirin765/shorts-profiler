# shorts-profiler MVP

Local-first MVP for short-form video tokenization and prompt generation.
Stack: FastAPI + Redis/RQ + Postgres.

## Requirements

- Python 3.11+
- Docker + Docker Compose
- `ffmpeg`
- `tesseract-ocr`

## Quick Start

```bash
cp .env.example .env
docker compose up --build
```

API base: `http://localhost:8000`

The one-command flow is:

1. PostgreSQL and Redis are started.
2. API and worker containers are started.
3. Open `http://localhost:8000` and use the minimal UI.

## API list

- `POST /videos/upload` : upload a file
  - form-data: `file` and optional `category_tag`
- `POST /videos/upload` : optional `source_url`
- `POST /jobs/analyze` : request body `{ "video_id": "..." }`
- `GET /jobs/{job_id}` : job status and progress
- `GET /videos/{video_id}/tokens` : token JSON (schema v1.0)
- `POST /videos/{video_id}/prompt` : body `{ "target": "sora|seedance|script|all" }`
- `GET /stats/summary` : query filters `category_tag`, `start_date`, `end_date`, `duration_bucket`
- `GET /stats/patterns/top` : query filters `category_tag`, `start_date`, `end_date`, `limit`

## API examples

```bash
# 1) Upload
curl -X POST "http://localhost:8000/videos/upload" -F "file=@sample.mp4" -F "category_tag=review"

# 2) enqueue analyze
curl -X POST "http://localhost:8000/jobs/analyze" -H "Content-Type: application/json" -d "{\"video_id\":\"<VIDEO_ID>\"}"

# 3) job status
curl -X GET "http://localhost:8000/jobs/<JOB_ID>"

# 4) tokens
curl -X GET "http://localhost:8000/videos/<VIDEO_ID>/tokens"

# 5) prompts
curl -X POST "http://localhost:8000/videos/<VIDEO_ID>/prompt" -H "Content-Type: application/json" -d "{\"target\":\"all\"}"

# 6) summary + patterns
curl -X GET "http://localhost:8000/stats/summary?category_tag=review&start_date=2026-01-01&end_date=2026-12-31&duration_bucket=0_10"
curl -X GET "http://localhost:8000/stats/patterns/top?category_tag=review&limit=5"
```

## Smoke test (PowerShell)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\smoke-test.ps1 -VideoPath "C:\path\to\your\video.mp4"
```

The script checks:
- API health
- upload
- analyze queue
- token generation
- prompt generation (`all`)
- summary/pattern stats

Outputs compact JSON with `video_id`, `job_id`, and token/prompt summary.

## Local run (without Docker)

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.api.main:app --reload
rq worker shorts -u redis://localhost:6379/0
```

## DB migration

```bash
alembic upgrade head
```

Generated tables from `migrations/versions/001_initial.py`:

- `videos`
- `jobs`
- `tokens`
- `prompts`

## Token schema (summary)

- `schema_version`
- `video_id`, `duration_sec`, `resolution`
- `hook`
- `editing`
- `subtitle`
- `visual`
- `audio`
- `structure`
- `notes`

Raw OCR text is not exposed. Generated output stores only normalized summaries/statistics.

## Security rules

- No raw subtitles/text are returned in prompt output.
- Prompt templates are generic and avoid repeated or identifying wording.
- Creator-identifying elements are not included.

## Operations

```bash
# check logs
docker compose logs -f api
docker compose logs -f worker

# health check
curl -X GET "http://localhost:8000/health"
```

- Failed jobs are marked `status=failed` with `error` message, tokens are not generated.
- OCR partial failures are written as warnings in `tokens.notes.warnings` and analysis continues.

## Upload flow example

```bash
git branch -M codex/shorts-profiler-mvp-20260303
git add .
git commit -m "feat: implement shorts-profiler mvp"
git remote add origin https://github.com/kirin765/shorts-profiler.git
git push -u origin codex/shorts-profiler-mvp-20260303
```
