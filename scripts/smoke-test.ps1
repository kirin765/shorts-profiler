param(
    [Parameter(Mandatory = $true)]
    [string]$VideoPath,
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$CategoryTag = "smoke-test",
    [int]$PollIntervalSeconds = 2,
    [int]$MaxWaitSeconds = 180
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $VideoPath)) {
    throw "Video file not found: $VideoPath"
}

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] $Message"
}

function Invoke-Health {
    param([string]$BaseUrl)
    try {
        $resp = Invoke-RestMethod -Uri "$BaseUrl/health" -Method Get -TimeoutSec 10
        if ($resp.status -ne 'ok') {
            throw "health response invalid: $($resp | ConvertTo-Json -Compress)"
        }
        return $true
    } catch {
        return $false
    }
}

Write-Log "Start smoke test"
if (-not (Invoke-Health -BaseUrl $BaseUrl)) {
    throw "API not healthy. Start docker compose first: docker compose up --build -d"
}

Write-Log "Uploading: $VideoPath"
$upload = Invoke-WebRequest -Method Post -Uri "$BaseUrl/videos/upload" -Form @{
    file = Get-Item -Path $VideoPath
    category_tag = $CategoryTag
}
$uploadJson = $upload.Content | ConvertFrom-Json
$videoId = [string]$uploadJson.video_id
if (-not $videoId) {
    throw "upload response has no video_id: $($upload.Content)"
}
Write-Log "Video uploaded: video_id=$videoId"

Write-Log "Start analyze"
$analyze = Invoke-RestMethod -Method Post -Uri "$BaseUrl/jobs/analyze" -ContentType 'application/json' -Body (@{video_id = $videoId} | ConvertTo-Json)
$jobId = [string]$analyze.job_id
Write-Log "Job started: job_id=$jobId"

$deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
$finalStatus = $null

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds $PollIntervalSeconds
    $status = Invoke-RestMethod -Uri "$BaseUrl/jobs/$jobId" -Method Get
    $finalStatus = [string]$status.status
    Write-Host "status=$finalStatus progress=$($status.progress) error=$($status.error)"
    if ($finalStatus -in @('done','failed')) { break }
}

if ($finalStatus -ne 'done') {
    throw "analysis did not complete. status=$finalStatus"
}

Write-Log "Fetch tokens"
$tokens = Invoke-RestMethod -Uri "$BaseUrl/videos/$videoId/tokens" -Method Get
$tokenData = $tokens.data
$schema = $tokenData.schema_version
$duration = $tokenData.duration_sec
$hookType = $tokenData.hook.hook_type
Write-Host "tokens: schema=$schema duration=$duration hook=$hookType"

Write-Log "Generate prompts (sora/seedance/script)"
$prompts = Invoke-RestMethod -Uri "$BaseUrl/videos/$videoId/prompt" -Method Post -ContentType 'application/json' -Body (@{target = 'all'} | ConvertTo-Json)
Write-Host "prompts: $($prompts.targets -join ', ')"

Write-Log "Fetch stats"
$summary = Invoke-RestMethod -Uri "$BaseUrl/stats/summary?category_tag=$([uri]::EscapeDataString($CategoryTag))" -Method Get
$patterns = Invoke-RestMethod -Uri "$BaseUrl/stats/patterns/top?category_tag=$([uri]::EscapeDataString($CategoryTag))&limit=5" -Method Get

Write-Log "Smoke test complete"
[PSCustomObject]@{
    video_id = $videoId
    job_id = $jobId
    status = $finalStatus
    tokens = @{
        schema_version = $schema
        duration_sec   = $duration
        hook_type      = $hookType
    }
    prompts = $prompts.targets
    summary_total = $summary.total_videos
    top_patterns = $patterns.top_patterns
} | ConvertTo-Json -Depth 6
