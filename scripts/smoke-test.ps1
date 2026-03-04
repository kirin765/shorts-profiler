param(
    [Parameter(Mandatory = $true)]
    [string]$VideoPath,
    [string]$YoutubeUrl = "",
    [string]$TikTokUrl = "",
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$CategoryTag = "smoke-test",
    [int]$PollIntervalSeconds = 2,
    [int]$MaxWaitSeconds = 240
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
        return $resp.status -eq 'ok'
    } catch {
        return $false
    }
}

function Parse-UploadCurlResult {
    param([string]$Raw)

    $marker = "__HTTP_STATUS__"
    $idx = $Raw.LastIndexOf($marker)
    if ($idx -lt 0) {
        throw "invalid curl response: $Raw"
    }

    $body = $Raw.Substring(0, $idx).Trim()
    $tail = $Raw.Substring($idx + $marker.Length)
    if ($tail -notmatch '(?s)^\s*(\d{3})\s*$') {
        throw "invalid curl status suffix: $tail"
    }

    return @{
        Body = $body
        Status = [int]$Matches[1]
    }
}

function Invoke-UploadFile {
    param(
        [string]$BaseUrl,
        [string]$VideoPath,
        [string]$CategoryTag
    )

    $uri = "$BaseUrl/videos/upload"
    $filePath = (Resolve-Path $VideoPath).Path

    $curlExe = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curlExe -ne $null) {
        $raw = & $curlExe.Source --silent --show-error --location --write-out "`n__HTTP_STATUS__%{http_code}" -X POST $uri -F "file=@$filePath" -F "category_tag=$CategoryTag"
        if ($LASTEXITCODE -ne 0) {
            throw "curl upload failed. code=$LASTEXITCODE body=$raw"
        }
        $parsed = Parse-UploadCurlResult -Raw $raw
        if ($parsed.Status -lt 200 -or $parsed.Status -ge 300) {
            throw "upload failed (HTTP $($parsed.Status)): $($parsed.Body)"
        }
        return $parsed.Body | ConvertFrom-Json
    }

    $client = [System.Net.Http.HttpClient]::new()
    try {
        $form = [System.Net.Http.MultipartFormDataContent]::new()
        $fileStream = [System.IO.File]::OpenRead($filePath)
        $fileContent = [System.Net.Http.StreamContent]::new($fileStream)
        $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new("application/octet-stream")
        $form.Add($fileContent, "file", [System.IO.Path]::GetFileName($filePath))
        $form.Add([System.Net.Http.StringContent]::new($CategoryTag), "category_tag")
        $response = $client.PostAsync($uri, $form).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            $err = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            throw "upload failed ($($response.StatusCode)): $err"
        }
        $raw = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return $raw | ConvertFrom-Json
    } finally {
        if ($fileContent -ne $null) { $fileContent.Dispose() }
        if ($form -ne $null) { $form.Dispose() }
        if ($fileStream -ne $null) { $fileStream.Dispose() }
        $client.Dispose()
    }
}

function Invoke-UploadUrl {
    param(
        [string]$BaseUrl,
        [string]$SourceUrl,
        [string]$CategoryTag
    )

    $uri = "$BaseUrl/videos/upload"
    try {
        $curlExe = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curlExe -ne $null) {
            $raw = & $curlExe.Source --silent --show-error --location --write-out "`n__HTTP_STATUS__%{http_code}" -X POST $uri -F "source_url=$SourceUrl" -F "category_tag=$CategoryTag"
            if ($LASTEXITCODE -ne 0) {
                throw "curl upload failed. code=$LASTEXITCODE body=$raw"
            }
            $parsed = Parse-UploadCurlResult -Raw $raw
            if ($parsed.Status -lt 200 -or $parsed.Status -ge 300) {
                throw "upload failed (HTTP $($parsed.Status)): $($parsed.Body)"
            }
            return $parsed.Body | ConvertFrom-Json
        }

        $client = [System.Net.Http.HttpClient]::new()
        $form = [System.Net.Http.MultipartFormDataContent]::new()
        $form.Add([System.Net.Http.StringContent]::new($SourceUrl), "source_url")
        $form.Add([System.Net.Http.StringContent]::new($CategoryTag), "category_tag")
        $response = $client.PostAsync($uri, $form).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            $err = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            throw "upload failed ($($response.StatusCode)): $err"
        }
        $raw = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return $raw | ConvertFrom-Json
    } catch {
        throw "URL upload failed: $($_.Exception.Message)"
    } finally {
        if ($form -ne $null) { $form.Dispose() }
        if ($client -ne $null) { $client.Dispose() }
    }
}

function Invoke-Analyze {
    param([string]$BaseUrl, [string]$VideoId)
    $analyze = Invoke-RestMethod -Method Post -Uri "$BaseUrl/jobs/analyze" -ContentType 'application/json' -Body (@{video_id = $VideoId} | ConvertTo-Json)
    return [string]$analyze.job_id
}

function Wait-JobDone {
    param(
        [string]$BaseUrl,
        [string]$JobId,
        [int]$PollSeconds,
        [int]$DeadlineSeconds
    )

    $deadline = (Get-Date).AddSeconds($DeadlineSeconds)
    $finalStatus = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds $PollSeconds
        $status = Invoke-RestMethod -Uri "$BaseUrl/jobs/$JobId" -Method Get
        $finalStatus = [string]$status.status
        Write-Host "status=$finalStatus progress=$($status.progress) error=$($status.error)"
        if ($finalStatus -in @('done','failed')) { return $finalStatus }
    }
    return $finalStatus
}

function Test-OneVideo {
    param(
        [string]$BaseUrl,
        [string]$VideoId,
        [string]$JobId
    )

    $finalStatus = Wait-JobDone -BaseUrl $BaseUrl -JobId $JobId -PollSeconds $PollIntervalSeconds -DeadlineSeconds $MaxWaitSeconds
    if ($finalStatus -ne 'done') {
        throw "analysis did not complete. status=$finalStatus"
    }

    $tokens = Invoke-RestMethod -Uri "$BaseUrl/videos/$VideoId/tokens" -Method Get
    $tokenData = $tokens.data
    $schema = $tokenData.schema_version
    $duration = $tokenData.duration_sec
    $hookType = $tokenData.hook.hook_type
    if (-not $tokenData.hook.PSObject.Properties.Name.Contains("hook_text_ocr")) {
        throw "hook_text_ocr missing in token payload"
    }
    if ($tokenData.hook.hook_text_ocr -and $tokenData.hook.hook_text_ocr.Length -gt 500) {
        throw "hook_text_ocr exceeds 500 chars"
    }
    if (-not $tokenData.structure.PSObject.Properties.Name.Contains("shots")) {
        Write-Log "WARNING: structure.shots missing (may be empty)"
    }
    if ($tokenData.structure.shots -ne $null -and $tokenData.structure.shots.Count -gt 0) {
        Write-Host "shots_count=$($tokenData.structure.shots.Count)"
    }

    Write-Host "tokens: schema=$schema duration=$duration hook=$hookType"

    $sora = Invoke-RestMethod -Uri "$BaseUrl/videos/$VideoId/prompt" -Method Post -ContentType 'application/json' -Body (@{target='sora'} | ConvertTo-Json)
    $seedance = Invoke-RestMethod -Uri "$BaseUrl/videos/$VideoId/prompt" -Method Post -ContentType 'application/json' -Body (@{target='seedance'} | ConvertTo-Json)
    $custom = Invoke-RestMethod -Uri "$BaseUrl/videos/$VideoId/prompt" -Method Post -ContentType 'application/json' -Body (@{target='gpt-4o-mini'} | ConvertTo-Json)

    return @{
        status = $finalStatus
        schema_version = $schema
        duration_sec = $duration
        hook_type = $hookType
        hooks_ocr_present = [bool]($tokenData.hook.hook_text_ocr)
        prompt_targets = (($sora.targets + $seedance.targets + $custom.targets) -join ',')
    }
}

Write-Log "Start smoke test"
if (-not (Invoke-Health -BaseUrl $BaseUrl)) {
    throw "API not healthy. Start docker compose first: docker compose up --build -d"
}

$results = @()

Write-Log "Uploading file: $VideoPath"
$uploadJson = Invoke-UploadFile -BaseUrl $BaseUrl -VideoPath $VideoPath -CategoryTag $CategoryTag
if (-not ($uploadJson -and $uploadJson.PSObject.Properties.Name -contains 'video_id' -and $uploadJson.video_id)) {
    throw "file upload response missing video_id"
}
$videoId = [string]$uploadJson.video_id
$jobId = Invoke-Analyze -BaseUrl $BaseUrl -VideoId $videoId
$results += [pscustomobject](Test-OneVideo -BaseUrl $BaseUrl -VideoId $videoId -JobId $jobId)

if ($YoutubeUrl) {
    Write-Log "Uploading YouTube URL"
    try {
        $uploadJson = Invoke-UploadUrl -BaseUrl $BaseUrl -SourceUrl $YoutubeUrl -CategoryTag "$CategoryTag-yt"
        if ($uploadJson -and $uploadJson.PSObject.Properties.Name -contains 'video_id' -and $uploadJson.video_id) {
            $videoId = [string]$uploadJson.video_id
            $jobId = Invoke-Analyze -BaseUrl $BaseUrl -VideoId $videoId
            $results += [pscustomobject](Test-OneVideo -BaseUrl $BaseUrl -VideoId $videoId -JobId $jobId)
        } else {
            Write-Host "WARNING: YouTube URL upload failed or returned no video_id; skipping."
        }
    } catch {
        Write-Host "WARNING: YouTube URL upload failed: $($_.Exception.Message)"
    }
}

if ($TikTokUrl) {
    Write-Log "Uploading TikTok URL"
    try {
        $uploadJson = Invoke-UploadUrl -BaseUrl $BaseUrl -SourceUrl $TikTokUrl -CategoryTag "$CategoryTag-tt"
        if ($uploadJson -and $uploadJson.PSObject.Properties.Name -contains 'video_id' -and $uploadJson.video_id) {
            $videoId = [string]$uploadJson.video_id
            $jobId = Invoke-Analyze -BaseUrl $BaseUrl -VideoId $videoId
            $results += [pscustomobject](Test-OneVideo -BaseUrl $BaseUrl -VideoId $videoId -JobId $jobId)
        } else {
            Write-Host "WARNING: TikTok URL upload failed or returned no video_id; skipping."
        }
    } catch {
        Write-Host "WARNING: TikTok URL upload failed: $($_.Exception.Message)"
    }
}

Write-Log "Fetch stats"
$summary = Invoke-RestMethod -Uri "$BaseUrl/stats/summary?category_tag=$([uri]::EscapeDataString($CategoryTag))" -Method Get
$patterns = Invoke-RestMethod -Uri "$BaseUrl/stats/patterns/top?category_tag=$([uri]::EscapeDataString($CategoryTag))&limit=5" -Method Get

Write-Log "Smoke test complete"
[PSCustomObject]@{
    results = $results
    summary_total = $summary.total_videos
    top_patterns = $patterns.top_patterns
} | ConvertTo-Json -Depth 6
