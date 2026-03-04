param(
    [Parameter(Mandatory = $true)]
    [string[]]$CsvPaths,
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$DefaultCategoryTag = "batch",
    [int]$RetryCount = 1,
    [int]$PollIntervalSeconds = 2,
    [int]$MaxWaitSeconds = 300,
    [string]$ResultCsvPath = "",
    [switch]$NoAnalyze
)

$ErrorActionPreference = "Stop"
$WarningPreference = "Continue"

if (-not $CsvPaths -or $CsvPaths.Count -eq 0) {
    throw "CsvPaths is required."
}

foreach ($path in $CsvPaths) {
    if (-not (Test-Path $path)) {
        throw "CSV not found: $path"
    }
}

function Resolve-CsvColumnValue {
    param(
        [psobject]$Row,
        [string[]]$Names
    )
    foreach ($n in $Names) {
        if ($Row.PSObject.Properties.Name -contains $n) {
            $val = $Row.$n
            if (-not [string]::IsNullOrWhiteSpace([string]$val)) {
                return [string]$val
            }
        }
    }
    return ""
}

function Invoke-UploadUrl {
    param(
        [string]$BaseUrl,
        [string]$SourceUrl,
        [string]$CategoryTag
    )

    $uri = "$BaseUrl/videos/upload"
    Add-Type -AssemblyName System.Net.Http
    $client = [System.Net.Http.HttpClient]::new()
    try {
        $form = [System.Net.Http.MultipartFormDataContent]::new()
        $form.Add([System.Net.Http.StringContent]::new($SourceUrl), "source_url")
        $form.Add([System.Net.Http.StringContent]::new($CategoryTag), "category_tag")
        $response = $client.PostAsync($uri, $form).GetAwaiter().GetResult()
        $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "upload failed ($($response.StatusCode)): $content"
        }
        return $content | ConvertFrom-Json
    } finally {
        if ($form -ne $null) { $form.Dispose() }
        $client.Dispose()
    }
}

function Invoke-Analyze {
    param([string]$BaseUrl, [string]$VideoId)
    $resp = Invoke-RestMethod -Method Post -Uri "$BaseUrl/jobs/analyze" -ContentType "application/json" -Body (@{video_id = $VideoId} | ConvertTo-Json)
    return [string]$resp.job_id
}

function Wait-JobDone {
    param(
        [string]$BaseUrl,
        [string]$JobId,
        [int]$PollSeconds,
        [int]$DeadlineSeconds
    )
    $deadline = (Get-Date).AddSeconds($DeadlineSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds $PollSeconds
        $status = Invoke-RestMethod -Uri "$BaseUrl/jobs/$JobId" -Method Get
        $state = [string]$status.status
        $progress = $status.progress
        Write-Host "  job=$JobId status=$state progress=$progress"
        if ($state -in @('done','failed')) {
            return $state
        }
    }
    return "timeout"
}

if (-not $ResultCsvPath) {
    $ResultCsvPath = Join-Path (Get-Location).Path "batch-upload-results-$(Get-Date -Format 'yyyyMMdd_HHmmss').csv"
}

$rows = @()
$allLinks = @()
foreach ($path in $CsvPaths) {
    $csvRows = Import-Csv -Path $path
    foreach ($r in $csvRows) {
        $url = Resolve-CsvColumnValue -Row $r -Names @("source_url", "url", "link")
        if ([string]::IsNullOrWhiteSpace($url)) {
            continue
        }
        $uriObj = $null
        if (-not [Uri]::TryCreate($url, [UriKind]::Absolute, [ref]$uriObj)) {
            Write-Warning "Invalid URL skipped: $url"
            continue
        }
        $url = $uriObj.AbsoluteUri
        $cat = Resolve-CsvColumnValue -Row $r -Names @("category_tag", "category")
        if ([string]::IsNullOrWhiteSpace($cat)) { $cat = $DefaultCategoryTag }
        $source = [pscustomobject]@{
            SourceUrl = [string]$url
            CategoryTag = [string]$cat
            SourceCsv = [string](Resolve-Path $path).Path
            SourceRow = $r
        }
        $allLinks += $source
    }
}

if ($allLinks.Count -eq 0) {
    throw "No valid URL rows found in provided CSV files."
}

Write-Host ("Total links: {0}" -f $allLinks.Count)

$deduped = @{}
$index = 0
foreach ($item in $allLinks) {
    $index++
    $url = $item.SourceUrl
    $categoryTag = $item.CategoryTag

    if ($deduped.ContainsKey($url)) {
        Write-Warning "Duplicate URL skipped: $url"
        $rows += [pscustomobject]@{
            source_csv = $item.SourceCsv
            source_url = $url
            category_tag = $categoryTag
            video_id = ""
            job_id = ""
            status = "skipped_dup"
            error = "duplicated_source_url"
            queued_at = (Get-Date).ToString("s")
            done_at = (Get-Date).ToString("s")
        }
        continue
    }
    $deduped[$url] = $true

    Write-Host ("[{0}/{1}] upload: {2}" -f $index, $allLinks.Count, $url)
    $started = Get-Date
    $uploadSucceeded = $false
    $videoId = ""
    $jobId = ""
    $status = "upload_failed"
    $errorMessage = ""

    for ($attempt = 1; $attempt -le ($RetryCount + 1); $attempt++) {
        try {
            $uploadResp = Invoke-UploadUrl -BaseUrl $BaseUrl -SourceUrl $url -CategoryTag $categoryTag
            if (-not ($uploadResp.PSObject.Properties.Name -contains "video_id") -or -not $uploadResp.video_id) {
                throw "upload response missing video_id"
            }
            $videoId = [string]$uploadResp.video_id
            $uploadSucceeded = $true
            break
        } catch {
            $errorMessage = $_.Exception.Message
            if ($attempt -le $RetryCount) {
                Write-Host "  upload retry $attempt/$RetryCount ..."
                Start-Sleep -Seconds 2
            }
        }
    }

    if (-not $uploadSucceeded) {
        Write-Host "  FAIL: $errorMessage"
        $rows += [pscustomobject]@{
            source_csv = $item.SourceCsv
            source_url = $url
            category_tag = $categoryTag
            video_id = ""
            job_id = ""
            status = "upload_failed"
            error = $errorMessage
            queued_at = $started.ToString("s")
            done_at = (Get-Date).ToString("s")
        }
        continue
    }

    if ($NoAnalyze) {
        $status = "uploaded"
        Write-Host "  uploaded video_id=$videoId"
        $rows += [pscustomobject]@{
            source_csv = $item.SourceCsv
            source_url = $url
            category_tag = $categoryTag
            video_id = $videoId
            job_id = ""
            status = $status
            error = ""
            queued_at = $started.ToString("s")
            done_at = (Get-Date).ToString("s")
        }
        continue
    }

    try {
        $jobId = Invoke-Analyze -BaseUrl $BaseUrl -VideoId $videoId
        Write-Host "  analyze queued job_id=$jobId"
        $status = Wait-JobDone -BaseUrl $BaseUrl -JobId $jobId -PollSeconds $PollIntervalSeconds -DeadlineSeconds $MaxWaitSeconds
    } catch {
        $status = "analyze_failed"
        $errorMessage = $_.Exception.Message
    }

    $rows += [pscustomobject]@{
        source_csv = $item.SourceCsv
        source_url = $url
        category_tag = $categoryTag
        video_id = $videoId
        job_id = $jobId
        status = $status
        error = $errorMessage
        queued_at = $started.ToString("s")
        done_at = (Get-Date).ToString("s")
    }
}

$rows | Export-Csv -Path $ResultCsvPath -NoTypeInformation -Encoding UTF8
Write-Host ("result_file={0}" -f $ResultCsvPath)

$summary = @{
    total = $rows.Count
    done = @($rows | Where-Object { $_.status -eq "done" }).Count
    failed = @($rows | Where-Object { $_.status -in @("upload_failed","analyze_failed","timeout") }).Count
    skipped = @($rows | Where-Object { $_.status -eq "skipped_dup" }).Count
}

$summary | ConvertTo-Json -Depth 5
