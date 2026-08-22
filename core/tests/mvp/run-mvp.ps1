<#
.SYNOPSIS
    WP 1.7 (Phase 1) — the MVP end-to-end test: prefill a game via curl,
    query its size, delete it, no app. Plan §7 Phase-1 milestone.

.DESCRIPTION
    Executes EVERYTHING via HTTP against a live vault-api instance (the
    "via curl" spirit of the milestone — this script is the curl caller,
    not a shortcut through Python internals), which in turn drives the real,
    already-logged-in SteamPrefill against the real Steam CDN for exactly
    ONE small app that the SteamPrefill account actually owns. There is
    deliberately no baked-in "safe" default app id — see -AppId below for
    why, and for what happened the one time this script assumed one.

    Steps (see docs/PROJECT_PLAN.md §7 and api/README.md):
      a. Ensure vault-core (production nginx config) is the one answering on
         port 80 — stop whatever nginx is currently running (this retires
         the Phase-0 PoC nginx as the resident cache), start core's config,
         verify /health + /lancache-heartbeat.
      b. Start vault-api (uvicorn) as a child process against a THROWAWAY
         SQLite DB, VAULT_CACHE_ROOT = core/cache, VAULT_STEAMPREFILL_PATH =
         the real, logged-in SteamPrefill binary.
      c. POST /v1/prefill {"appids":[<AppId>]} -> 202 + job id.
      d. Poll GET /v1/jobs/{id} (bounded, default 10 min) until done/error.
      e. GET /v1/games/{AppId} (size_bytes > 0, >=1 depot) and
         GET /v1/cache/summary ({AppId} present, total_bytes >= its size).
      f. Filesystem cross-check: depot dirs + 40-hex-SHA1 chunk files exist
         under core/cache/depot/ -- recorded into the RESULTS evidence, not
         just the console.
      g. DELETE /v1/cache/{AppId} -> 200, total_bytes_freed > 0 (shared
         depots, if any, are recorded, not asserted away); GET
         /v1/games/{AppId} again -- ONLY asserted against if the DELETE
         itself actually returned 200 (see the "gating" note below);
         filesystem shows the exclusive depots gone.
      h. Teardown: stop the vault-api child, restore SteamPrefill's
         selectedAppsToPrefill.json to what it held before this script ran,
         clear the VAULT_* env vars this run set, LEAVE vault-core (this
         script's nginx) running as the resident cache, remove the
         throwaway DB. Write RESULTS-<timestamp>.md with every
         request/response (API key redacted) plus the key numbers and
         wall-clock timings.

    This is a REAL prefill against Valve's CDN with the user's own Steam
    account, once, for one small app. It does not run select-apps, does not
    touch the login, and does not prefill anything beyond -AppId.

    Exit code 0 = PASS, 1 = FAIL/blocked. A [FAIL] does not stop the run
    early — teardown (h) always happens (restoring the selection file and
    leaving core nginx running matter regardless of outcome) — but it does
    make the process exit non-zero and the RESULTS file record every
    failure honestly, including a blocked run (e.g. an expired SteamPrefill
    session), rather than papering over it.

    Gating note (fixed after review): a job reaching vault-api status
    'done' means SteamPrefill's *process* exited 0 -- it does NOT mean any
    depot was actually observed/mapped. The size/summary assertions in (e)
    and the post-delete assertions in (g) are gated on the app actually
    having a positive, mapped size (and, for (g), on DELETE itself having
    returned 200) -- not merely on the job status -- specifically because
    the first real run below hit exactly this gap: the job finished
    'done', size_bytes was null, DELETE 404'd (nothing to delete), and an
    earlier version of this script still printed spurious [ OK ] lines for
    "total_bytes >= size_bytes" (PowerShell: anything -ge $null is true)
    and for the post-delete size check (null -eq null, whether or not a
    delete had actually happened).

.PARAMETER AppId
    Steam app id to prefill/query/delete. MANDATORY, NO DEFAULT -- the
    operator MUST set this to an app id that the account behind
    VAULT_STEAMPREFILL_PATH actually owns. Confirm this first with
    `SteamPrefill.exe select-apps status` (a read-only, non-interactive
    check) or by consulting known-good evidence in this repo, NOT by
    guessing from a game's reputation as "small."

    This is not a hypothetical caveat: the first real run of this script
    used 480 (Spacewar) on the strength of the work-package brief's "small,
    owned, proven in Phase 0" -- and that was wrong for this account.
    Spacewar only shows up in SteamPrefill's owned-apps list if the
    account's Steamworks history includes it
    (poc/steamprefill/PROTOCOL.md §3.2); this account's doesn't. SteamPrefill
    still ran without error (real login, real PICS query) but resolved zero
    depots and reported "Prefilled 0 apps totaling 0 b" / "Up To Date" --
    see RESULTS-20260805-222046.md for the full blocked-run evidence (job
    status 'done', yet nothing to size or delete -- the honest, documented
    404/null behavior, not a vault-api bug).

    The rerun with -AppId 3419430 (this account's own confirmed-owned small
    app, already prefilled successfully through SteamPrefill in Phase 0 --
    see poc/steamprefill/RESULTS-STEAMPREFILL-20260804-195348.md: depots
    242921 + 3419431, ~200 MiB) passed end-to-end; see the RESULTS file
    from that run for the proven-good shape of a passing report.

.PARAMETER JobTimeoutSeconds
    Bounded wait for the prefill job to leave 'queued'/'running'. Default
    600 (10 minutes) per the work package.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int]$AppId,
    [int]$JobTimeoutSeconds = 600,
    [string]$ApiBindHost = "127.0.0.1",
    [int]$ApiPort = 8080
)

$ErrorActionPreference = "Stop"

# --- paths --------------------------------------------------------------------
$MvpRoot   = $PSScriptRoot                        # .../core/tests/mvp
$CoreTests = Split-Path $MvpRoot -Parent          # .../core/tests
$CoreRoot  = Split-Path $CoreTests -Parent        # .../core
$RepoRoot  = Split-Path $CoreRoot -Parent         # repo root
$PocRoot   = Join-Path $RepoRoot "poc"
$ApiRoot   = Join-Path $RepoRoot "api"

$NginxExe   = Join-Path $PocRoot "nginx\nginx.exe"
$CoreConfig = "nginx/nginx.conf"                  # relative to -p $CoreRoot

$PythonExe  = Join-Path $ApiRoot ".venv\Scripts\python.exe"
$SteamPrefillExe = Join-Path $PocRoot "steamprefill\bin\SteamPrefill.exe"
$SelectionFile   = Join-Path $PocRoot "steamprefill\bin\Config\selectedAppsToPrefill.json"

$RunTmp = Join-Path $MvpRoot "_run_tmp"
if (-not (Test-Path $RunTmp)) { New-Item -ItemType Directory -Path $RunTmp -Force | Out-Null }

$Timestamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$ResultsFile = Join-Path $MvpRoot "RESULTS-$Timestamp.md"
$ThrowawayDb = Join-Path $RunTmp "vault-mvp-$Timestamp.db"
$ApiStdoutLog = Join-Path $RunTmp "api-stdout-$Timestamp.log"
$ApiStderrLog = Join-Path $RunTmp "api-stderr-$Timestamp.log"

$BaseUrl = "http://${ApiBindHost}:${ApiPort}"
# Throwaway secret for this run only -- never written to any .env, never
# reused across runs, redacted before it ever reaches RESULTS-*.md.
$ApiKey = -join ((48..57 + 65..90 + 97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })

$script:failures = @()
$script:evidence = [System.Collections.Generic.List[string]]::new()
$script:numbers  = [ordered]@{}
$script:timings  = [ordered]@{}
$swTotal = [System.Diagnostics.Stopwatch]::StartNew()

function Step($msg) { Write-Host ""; Write-Host "[STEP] $msg" -ForegroundColor Cyan }
function Pass($msg) { Write-Host "  [ OK ] $msg" -ForegroundColor Green }
function Fail($msg) { $script:failures += $msg; Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Info($msg) { Write-Host "  [INFO] $msg" -ForegroundColor Yellow }

function Mark-Timing([string]$label) {
    $script:timings[$label] = "{0:N1}s" -f $swTotal.Elapsed.TotalSeconds
}

function Record-Note([string]$title, [string]$body) {
    $script:evidence.Add("### $title`n`n$body`n")
}

# Multi-KB/multi-line content (a job's log_excerpt, a filesystem listing)
# must never go into the $script:numbers table -- that table is rendered as
# a one-row-per-metric Markdown table, and a value containing real newlines
# breaks the table into unreadable garbage (observed exactly this way in the
# first real run's RESULTS file). Long/multi-line content always goes here
# instead, as its own fenced section. Built with a single-quoted fence
# literal rather than escaped backticks in a double-quoted string, and `n
# (PowerShell's own newline escape) rather than a literal "\n" (a bash/C
# escape that PowerShell does NOT interpret in a double-quoted string --
# the bug an earlier version of this script had in its error-path log dump).
function Record-Block([string]$title, [string]$body) {
    $fence = '```'
    $script:evidence.Add("### $title`n`n$fence`n$body`n$fence")
}

# --- HTTP helper: logs redacted request/response into $script:evidence -------
function Invoke-VaultApi {
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        [string]$RawBody = $null
    )
    $uri = "$BaseUrl$Path"
    $headers = @{ "X-Api-Key" = $ApiKey }
    $params = @{ Method = $Method; Uri = $uri; Headers = $headers; TimeoutSec = 60; UseBasicParsing = $true }
    if ($RawBody) {
        $params.Body = $RawBody
        $params.ContentType = "application/json"
    }

    $statusCode = $null
    $responseBody = $null
    try {
        $resp = Invoke-WebRequest @params
        $statusCode = [int]$resp.StatusCode
        $responseBody = $resp.Content
    }
    catch {
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
            try {
                $stream = $_.Exception.Response.GetResponseStream()
                $reader = New-Object System.IO.StreamReader($stream)
                $responseBody = $reader.ReadToEnd()
            }
            catch { $responseBody = "(could not read error response body: $_)" }
        }
        else {
            $responseBody = "(request threw with no HTTP response: $_)"
        }
    }

    $bodyForLog = if ($RawBody) { $RawBody } else { "(none)" }
    $entry = @"
**$Method $Path**
Headers: ``X-Api-Key: <redacted>``
Request body: ``$bodyForLog``

Response: HTTP $statusCode
``````
$responseBody
``````
"@
    $script:evidence.Add($entry)
    return [PSCustomObject]@{ StatusCode = $statusCode; Body = $responseBody }
}

# --- nginx pid-file helpers (same pattern as core/tests/test-core.ps1) --------
function Get-PidFromFile([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return $null }
    $raw = Get-Content $PidFile -Raw -ErrorAction SilentlyContinue
    if (-not $raw) { return $null }
    $parsed = 0
    if ([int]::TryParse($raw.Trim(), [ref]$parsed)) { return $parsed }
    return $null
}

function Stop-NginxInstance([string]$Prefix, [string]$ConfigRelPath, [string]$PidFile) {
    $masterPid = Get-PidFromFile $PidFile
    if (-not $masterPid) { return }
    if (-not (Get-Process -Id $masterPid -ErrorAction SilentlyContinue)) { return }
    try { & $NginxExe -p "$Prefix" -c $ConfigRelPath -s stop 2>$null } catch {}
    Start-Sleep -Milliseconds 500
    if (Get-Process -Id $masterPid -ErrorAction SilentlyContinue) {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$masterPid" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $masterPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
    }
}

function Start-CoreNginxResident {
    foreach ($dir in @(
        (Join-Path $CoreRoot "cache\depot"),
        (Join-Path $CoreRoot "tmp\client_body"),
        (Join-Path $CoreRoot "tmp\proxy"),
        (Join-Path $CoreRoot "tmp\fastcgi"),
        (Join-Path $CoreRoot "tmp\uwsgi"),
        (Join-Path $CoreRoot "tmp\scgi"),
        (Join-Path $CoreRoot "logs")
    )) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }

    # Stop whatever is currently on port 80 -- the PoC instance (if a logged-in
    # Steam client has it running right now) or a stale core instance.
    Stop-NginxInstance $PocRoot "conf/nginx.conf" (Join-Path $PocRoot "logs\nginx.pid")
    Stop-NginxInstance $CoreRoot $CoreConfig (Join-Path $CoreRoot "logs\nginx.pid")

    Write-Host "Starting vault-core nginx (prefix=$CoreRoot, config=$CoreConfig) on port 80 ..."
    Start-Process -FilePath $NginxExe `
        -ArgumentList @("-p", "`"$CoreRoot`"", "-c", $CoreConfig) `
        -WorkingDirectory $CoreRoot `
        -WindowStyle Hidden
    Start-Sleep -Milliseconds 800

    $corePidFile = Join-Path $CoreRoot "logs\nginx.pid"
    $corePid = Get-PidFromFile $corePidFile
    if (-not $corePid -or -not (Get-Process -Id $corePid -ErrorAction SilentlyContinue)) {
        throw "vault-core nginx did not start -- check core/logs/error.log"
    }
    Write-Host "vault-core nginx started (PID $corePid) -- this instance stays resident after this script exits." -ForegroundColor Green
    return $corePid
}

# ==============================================================================
Write-Host "== SteamHangar WP 1.7 -- MVP end-to-end test ==" -ForegroundColor Cyan
Write-Host "AppId=$AppId  BaseUrl=$BaseUrl  JobTimeoutSeconds=$JobTimeoutSeconds"

# --- prerequisite check (fail fast, before touching anything) ----------------
Step "Prerequisite check"
$prereqOk = $true
foreach ($p in @($NginxExe, $PythonExe, $SteamPrefillExe, $SelectionFile)) {
    if (-not (Test-Path $p)) {
        Fail "required file missing: $p"
        $prereqOk = $false
    }
}
if ($prereqOk) { Pass "nginx.exe, api venv python, SteamPrefill.exe and its selection file all present" }
else {
    Write-Host "Aborting before touching nginx/api -- see failures above." -ForegroundColor Red
    exit 1
}

# Back up the user's own SteamPrefill selection now, before anything can touch it.
$SelectionBackup = Get-Content $SelectionFile -Raw
Info "Backed up SteamPrefill selection file content: $SelectionBackup"

$apiProcess = $null
$corePid = $null

try {
    # --- (a) core nginx resident on port 80 -----------------------------------
    Step "(a) Ensure vault-core nginx is resident on port 80"
    $corePid = Start-CoreNginxResident
    Mark-Timing "core nginx started"

    try {
        $health = Invoke-WebRequest -Uri "http://127.0.0.1/health" -UseBasicParsing -TimeoutSec 10
        if ($health.StatusCode -eq 200) { Pass "/health returns 200" }
        else { Fail "/health returned HTTP $($health.StatusCode)" }
    }
    catch { Fail "/health request failed: $_" }

    try {
        $hb = Invoke-WebRequest -Uri "http://127.0.0.1/lancache-heartbeat" -UseBasicParsing -TimeoutSec 10
        if ($hb.StatusCode -eq 200 -and $hb.Headers['X-LanCache-Processed-By'] -eq "steamhangar") {
            Pass "/lancache-heartbeat: 200 + X-LanCache-Processed-By: steamhangar"
        }
        else {
            Fail "/lancache-heartbeat unexpected: HTTP $($hb.StatusCode), header='$($hb.Headers['X-LanCache-Processed-By'])'"
        }
    }
    catch { Fail "/lancache-heartbeat request failed: $_" }

    # --- (b) start vault-api ---------------------------------------------------
    Step "(b) Start vault-api (uvicorn) against a throwaway DB"
    $env:VAULT_API_KEY = $ApiKey
    $env:VAULT_DB_PATH = $ThrowawayDb
    $env:VAULT_CACHE_ROOT = (Join-Path $CoreRoot "cache")
    $env:VAULT_LOG_LEVEL = "INFO"
    $env:VAULT_STEAMPREFILL_PATH = $SteamPrefillExe
    $env:VAULT_PREFILL_TIMEOUT_SECONDS = "14400"
    $env:VAULT_WORKER_POLL_SECONDS = "1.0"
    $env:VAULT_SIZE_CACHE_TTL = "5"

    Info "VAULT_DB_PATH=$($env:VAULT_DB_PATH)"
    Info "VAULT_CACHE_ROOT=$($env:VAULT_CACHE_ROOT)"
    Info "VAULT_STEAMPREFILL_PATH=$($env:VAULT_STEAMPREFILL_PATH)"

    $apiProcess = Start-Process -FilePath $PythonExe `
        -ArgumentList @("-m", "uvicorn", "vault_api.main:create_app", "--factory", "--host", $ApiBindHost, "--port", "$ApiPort") `
        -WorkingDirectory $ApiRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $ApiStdoutLog `
        -RedirectStandardError $ApiStderrLog `
        -PassThru

    Start-Sleep -Milliseconds 500
    if ($apiProcess.HasExited) {
        Fail "vault-api process exited immediately (exit code $($apiProcess.ExitCode)) -- see $ApiStderrLog"
    }
    else {
        Pass "vault-api process started (PID $($apiProcess.Id))"
    }

    Info "Waiting for GET /v1/health ..."
    $healthy = $false
    $healthDeadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $healthDeadline) {
        try {
            $h = Invoke-WebRequest -Uri "$BaseUrl/v1/health" -UseBasicParsing -TimeoutSec 3
            if ($h.StatusCode -eq 200) { $healthy = $true; break }
        }
        catch { }
        Start-Sleep -Milliseconds 500
    }
    if ($healthy) { Pass "GET /v1/health is 200 -- vault-api is up" }
    else { Fail "vault-api never answered /v1/health within 30s -- see $ApiStderrLog" }
    Mark-Timing "vault-api healthy"

    if (-not $healthy) {
        throw "vault-api did not come up; aborting the rest of the run (teardown still executes)."
    }

    # --- (c) POST /v1/prefill --------------------------------------------------
    Step "(c) POST /v1/prefill {appids:[$AppId]}"
    $prefillResp = Invoke-VaultApi -Method POST -Path "/v1/prefill" -RawBody "{`"appids`": [$AppId]}"
    $jobId = $null
    if ($prefillResp.StatusCode -eq 202) {
        $prefillJson = $prefillResp.Body | ConvertFrom-Json
        $entryForApp = $prefillJson | Where-Object { $_.appid -eq $AppId } | Select-Object -First 1
        if ($entryForApp) {
            $jobId = $entryForApp.job_id
            Pass "202 Accepted -- job id $jobId for app $AppId (deduplicated=$($entryForApp.deduplicated))"
        }
        else {
            Fail "202 response did not contain an entry for appid $AppId : $($prefillResp.Body)"
        }
    }
    else {
        Fail "POST /v1/prefill returned HTTP $($prefillResp.StatusCode), expected 202: $($prefillResp.Body)"
    }

    if (-not $jobId) {
        throw "no job id -- cannot continue (teardown still executes)."
    }

    # --- (d) poll GET /v1/jobs/{id} until done/error, bounded ------------------
    Step "(d) Poll GET /v1/jobs/$jobId until done/error (timeout ${JobTimeoutSeconds}s)"
    $jobDeadline = (Get-Date).AddSeconds($JobTimeoutSeconds)
    $jobStatus = $null
    $jobJson = $null
    $pollCount = 0
    while ((Get-Date) -lt $jobDeadline) {
        $jobResp = Invoke-VaultApi -Method GET -Path "/v1/jobs/$jobId"
        $pollCount++
        if ($jobResp.StatusCode -ne 200) {
            Fail "GET /v1/jobs/$jobId returned HTTP $($jobResp.StatusCode) while polling"
            break
        }
        $jobJson = $jobResp.Body | ConvertFrom-Json
        $jobStatus = $jobJson.status
        if ($pollCount % 5 -eq 1) { Info "job $jobId status=$jobStatus (poll #$pollCount)" }
        if ($jobStatus -eq "done" -or $jobStatus -eq "error") { break }
        Start-Sleep -Seconds 3
    }
    Mark-Timing "prefill job finished (status=$jobStatus)"

    # A single-line derived fact is safe for the numbers table; the full
    # (potentially multi-KB, multi-line) excerpt always goes to Record-Block
    # instead (see that function's comment for why).
    $lastLogLine = $null
    if ($jobJson -and $jobJson.log_excerpt) {
        $lastLogLine = ($jobJson.log_excerpt -split "`n" | Where-Object { $_.Trim() -ne "" } | Select-Object -Last 1)
    }

    if ($jobStatus -eq "done") {
        Pass "job $jobId finished with status 'done' after $pollCount poll(s)"
        $script:numbers["job_log_last_line"] = $lastLogLine
        Record-Block "Job $jobId log_excerpt (status: done)" $jobJson.log_excerpt
    }
    elseif ($jobStatus -eq "error") {
        Fail "job $jobId finished with status 'error' -- BLOCKED run. log_excerpt follows:"
        Write-Host "----- log_excerpt -----" -ForegroundColor Red
        Write-Host $jobJson.log_excerpt
        Write-Host "-----------------------" -ForegroundColor Red
        $script:numbers["job_log_last_line"] = $lastLogLine
        Record-Block "Job $jobId log_excerpt (status: error)" $jobJson.log_excerpt
    }
    else {
        Fail "job $jobId did not reach done/error within ${JobTimeoutSeconds}s (last status: $jobStatus) -- treating as blocked"
    }

    $jobSucceeded = ($jobStatus -eq "done")

    # --- (e) size + summary (only meaningful if the job actually succeeded) ---
    Step "(e) GET /v1/games/$AppId and GET /v1/cache/summary"
    $gameResp = Invoke-VaultApi -Method GET -Path "/v1/games/$AppId"
    $gameJson = $null
    $depotsBeforeDelete = @()
    # Gates every numeric comparison below (step e AND step g). A job
    # reaching status 'done' means the SteamPrefill *process* exited 0 -- it
    # does NOT mean anything was actually observed/mapped (see the "Gating
    # note" in this script's header for the real run that hit exactly this:
    # job 'done', size_bytes null, and -- without this flag -- a bogus
    # [ OK ] for "total_bytes >= size_bytes" because PowerShell's -ge treats
    # $null as 0, so anything -ge $null is trivially true).
    $appSizeKnownGood = $false
    if ($gameResp.StatusCode -eq 200) {
        $gameJson = $gameResp.Body | ConvertFrom-Json
        $depotsBeforeDelete = @($gameJson.depots)
        $script:numbers["size_bytes_before_delete"] = $gameJson.size_bytes
        $script:numbers["depot_count_before_delete"] = $depotsBeforeDelete.Count

        if ($jobSucceeded) {
            $sizePositive = ($null -ne $gameJson.size_bytes) -and ($gameJson.size_bytes -gt 0)
            if ($sizePositive) { Pass "size_bytes = $($gameJson.size_bytes) (> 0)" }
            else { Fail "size_bytes was $($gameJson.size_bytes), expected > 0" }

            $hasDepots = $depotsBeforeDelete.Count -ge 1
            if ($hasDepots) { Pass "$($depotsBeforeDelete.Count) depot(s) mapped: $(($depotsBeforeDelete | ForEach-Object { $_.depotid }) -join ', ')" }
            else { Fail "no depots mapped to app $AppId after a 'done' job" }

            $appSizeKnownGood = $sizePositive -and $hasDepots
        }
        else {
            Info "job did not succeed -- recording GET /v1/games/$AppId as-is without asserting size/depots"
        }
    }
    else {
        Fail "GET /v1/games/$AppId returned HTTP $($gameResp.StatusCode)"
    }

    $summaryResp = Invoke-VaultApi -Method GET -Path "/v1/cache/summary"
    if ($summaryResp.StatusCode -eq 200) {
        $summaryJson = $summaryResp.Body | ConvertFrom-Json
        $script:numbers["cache_summary_total_bytes"] = $summaryJson.total_bytes
        $consumerEntry = $summaryJson.top_consumers | Where-Object { $_.appid -eq $AppId } | Select-Object -First 1
        if ($appSizeKnownGood) {
            if ($consumerEntry) { Pass "app $AppId present in top_consumers with size_bytes=$($consumerEntry.size_bytes)" }
            else { Fail "app $AppId NOT present in top_consumers: $($summaryResp.Body)" }

            if ($summaryJson.total_bytes -ge $gameJson.size_bytes) {
                Pass "cache summary total_bytes ($($summaryJson.total_bytes)) >= app $AppId size_bytes ($($gameJson.size_bytes))"
            }
            else {
                Fail "cache summary total_bytes ($($summaryJson.total_bytes)) < app $AppId size_bytes ($($gameJson.size_bytes))"
            }
        }
        else {
            Info "skipped top_consumers/total_bytes comparison -- app $AppId has no positive mapped size_bytes yet (see step e above), so the comparison would be against null/zero and prove nothing"
        }
    }
    else {
        Fail "GET /v1/cache/summary returned HTTP $($summaryResp.StatusCode)"
    }
    Mark-Timing "size + summary checked"

    # --- (f) filesystem cross-check --------------------------------------------
    Step "(f) Filesystem cross-check under core/cache/depot/"
    $depotRoot = Join-Path $CoreRoot "cache\depot"
    $chunkRegex = '^[0-9a-fA-F]{40}$'
    $fsLines = [System.Collections.Generic.List[string]]::new()
    $totalChunkFiles = 0
    if ($appSizeKnownGood) {
        foreach ($d in $depotsBeforeDelete) {
            $depotDir = Join-Path $depotRoot "$($d.depotid)"
            if (-not (Test-Path $depotDir)) {
                Fail "depot dir missing on disk: $depotDir"
                $fsLines.Add("depot $($d.depotid): MISSING on disk at $depotDir")
                continue
            }
            $chunkDir = Join-Path $depotDir "chunk"
            if (-not (Test-Path $chunkDir)) {
                Fail "no chunk/ subdir under $depotDir"
                $fsLines.Add("depot $($d.depotid): no chunk/ subdir under $depotDir")
                continue
            }
            $chunkFiles = Get-ChildItem -Path $chunkDir -File -ErrorAction SilentlyContinue
            if (-not $chunkFiles -or $chunkFiles.Count -eq 0) {
                Fail "depot $($d.depotid) chunk/ directory is empty"
                $fsLines.Add("depot $($d.depotid): chunk/ directory is EMPTY")
                continue
            }
            $badNames = $chunkFiles | Where-Object { $_.Name -notmatch $chunkRegex }
            $totalChunkFiles += $chunkFiles.Count
            if ($badNames) {
                Fail "depot $($d.depotid) has $($badNames.Count) chunk filename(s) that are not 40-hex-SHA1: $(($badNames | Select-Object -First 3 -ExpandProperty Name) -join ', ')"
                $fsLines.Add("depot $($d.depotid): $($chunkFiles.Count) chunk file(s), $($badNames.Count) with a NON-conforming name (first few: $(($badNames | Select-Object -First 3 -ExpandProperty Name) -join ', '))")
            }
            else {
                Pass "depot $($d.depotid): $($chunkFiles.Count) chunk file(s), all 40-hex-SHA1 names"
                $fsLines.Add("depot $($d.depotid): $($chunkFiles.Count) chunk file(s) under $chunkDir, ALL match ^[0-9a-fA-F]{40}$ (verified: $(($chunkFiles | Select-Object -First 3 -ExpandProperty Name) -join ', ')$(if ($chunkFiles.Count -gt 3) { ', ...' }))")
            }
        }
        $script:numbers["chunk_files_pre_delete"] = $totalChunkFiles
        Record-Block "Filesystem cross-check (pre-delete, core/cache/depot/)" ($fsLines -join "`n")
    }
    else {
        Info "skipped -- job did not succeed or app has no positive mapped size"
        Record-Note "Filesystem cross-check (pre-delete)" "Skipped -- job did not succeed or app $AppId has no positive mapped size (see step e)."
    }
    Mark-Timing "filesystem cross-check done (pre-delete)"

    # --- (g) DELETE /v1/cache/{appid} ------------------------------------------
    Step "(g) DELETE /v1/cache/$AppId"
    $deleteResp = Invoke-VaultApi -Method DELETE -Path "/v1/cache/$AppId"
    $deleteJson = $null
    # Gates every post-delete assertion below. DELETE returning something
    # other than 200 (e.g. 404 -- "nothing to delete", exactly what happened
    # in the blocked first run) means no deletion occurred at all, so any
    # "size is now null/reduced" or "depot dir is now gone" assertion would
    # be comparing against a state nothing here caused. Without this gate,
    # an earlier version of this script printed a spurious [ OK ] for
    # "size_bytes=null after delete" purely because the size had already
    # been null before the (404, no-op) delete was even attempted.
    $deleteSucceeded = ($deleteResp.StatusCode -eq 200)
    if ($deleteSucceeded) {
        $deleteJson = $deleteResp.Body | ConvertFrom-Json
        $script:numbers["total_bytes_freed"] = $deleteJson.total_bytes_freed
        $script:numbers["deleted_depots_count"] = @($deleteJson.deleted_depots).Count
        $script:numbers["skipped_shared"] = ($deleteJson.skipped_shared | ConvertTo-Json -Compress)
        $script:numbers["failed_count"] = @($deleteJson.failed).Count
        Pass "200 -- deleted=$(@($deleteJson.deleted_depots).Count) skipped_shared=$(@($deleteJson.skipped_shared).Count) failed=$(@($deleteJson.failed).Count) total_bytes_freed=$($deleteJson.total_bytes_freed)"

        if ($appSizeKnownGood) {
            if ($deleteJson.total_bytes_freed -gt 0) { Pass "total_bytes_freed = $($deleteJson.total_bytes_freed) (> 0)" }
            else { Fail "total_bytes_freed was $($deleteJson.total_bytes_freed), expected > 0" }
        }
        if (@($deleteJson.skipped_shared).Count -gt 0) {
            Info "skipped_shared recorded (not treated as a failure, plan §4 shared-depot protection): $($script:numbers['skipped_shared'])"
        }
        else {
            Info "no shared depots for app $AppId in this fresh DB"
        }
    }
    else {
        Fail "DELETE /v1/cache/$AppId returned HTTP $($deleteResp.StatusCode): $($deleteResp.Body)"
    }

    # GET /v1/games/{appid} again -- mapping rows survive deletion by design
    # (api/README.md "The mapping rows are KEPT"), so a 200 is always
    # expected regardless of whether anything was actually deleted (checked
    # unconditionally below). The size_bytes value assertion, however, is
    # only meaningful when a deletion actually happened -- see the gating
    # note above.
    $gameAfterResp = Invoke-VaultApi -Method GET -Path "/v1/games/$AppId"
    if ($gameAfterResp.StatusCode -eq 200) {
        $gameAfterJson = $gameAfterResp.Body | ConvertFrom-Json
        $script:numbers["size_bytes_after_delete"] = $gameAfterJson.size_bytes
        if ($deleteSucceeded) {
            $hadSharedDepots = $deleteJson -and (@($deleteJson.skipped_shared).Count -gt 0)
            if ($hadSharedDepots) {
                if ($null -ne $gameAfterJson.size_bytes -and $gameAfterJson.size_bytes -gt 0) {
                    Pass "GET /v1/games/$AppId after delete: 200, size_bytes=$($gameAfterJson.size_bytes) (non-null -- a shared depot survived, per README)"
                }
                else {
                    Fail "GET /v1/games/$AppId after delete: expected a non-null size_bytes because a shared depot was kept, got $($gameAfterJson.size_bytes)"
                }
            }
            else {
                if ($null -eq $gameAfterJson.size_bytes) {
                    Pass "GET /v1/games/$AppId after delete: 200, size_bytes=null (all depots were exclusive and removed, mapping rows kept per README)"
                }
                else {
                    Fail "GET /v1/games/$AppId after delete: expected size_bytes null, got $($gameAfterJson.size_bytes)"
                }
            }
        }
        else {
            Info "skipped size_bytes-after-delete assertion -- DELETE did not return 200, so no deletion happened to assert about (size_bytes=$($gameAfterJson.size_bytes) recorded as-is)"
        }
    }
    else {
        Fail "GET /v1/games/$AppId after delete returned HTTP $($gameAfterResp.StatusCode) (README says mapping rows survive deletion -- 404 would be a regression)"
    }

    # Filesystem: exclusive depots gone, shared ones (if any) still present.
    # Gated on $deleteSucceeded (not $jobSucceeded): $deleteJson is only ever
    # populated when DELETE returned 200, so this was already implicitly
    # safe, but the explicit gate documents the intent and matches the (g)
    # assertion above rather than relying on that side effect.
    $postDeleteFsLines = [System.Collections.Generic.List[string]]::new()
    if ($deleteSucceeded -and $deleteJson) {
        foreach ($dd in @($deleteJson.deleted_depots)) {
            $depotDir = Join-Path $depotRoot "$($dd.depotid)"
            if (Test-Path $depotDir) {
                Fail "depot dir $depotDir still exists on disk after being reported deleted"
                $postDeleteFsLines.Add("depot $($dd.depotid): STILL PRESENT at $depotDir (should be gone)")
            }
            else {
                Pass "depot dir for deleted depot $($dd.depotid) is gone from disk"
                $postDeleteFsLines.Add("depot $($dd.depotid): gone from disk (was at $depotDir), $($dd.size_bytes_freed) bytes freed")
            }
        }
        foreach ($sd in @($deleteJson.skipped_shared)) {
            $depotDir = Join-Path $depotRoot "$($sd.depotid)"
            if (Test-Path $depotDir) {
                Pass "shared depot $($sd.depotid) correctly still present on disk (shared with app(s) $($sd.shared_with -join ', '))"
                $postDeleteFsLines.Add("depot $($sd.depotid): correctly still present at $depotDir (shared with app(s) $($sd.shared_with -join ', '))")
            }
            else {
                Fail "shared depot $($sd.depotid) is missing from disk -- should have been kept"
                $postDeleteFsLines.Add("depot $($sd.depotid): MISSING from disk (should have been kept, shared with $($sd.shared_with -join ', '))")
            }
        }
        Record-Block "Filesystem cross-check (post-delete, core/cache/depot/)" ($postDeleteFsLines -join "`n")
    }
    else {
        Info "skipped post-delete filesystem cross-check -- DELETE did not return 200"
        Record-Note "Filesystem cross-check (post-delete)" "Skipped -- DELETE /v1/cache/$AppId did not return 200 (HTTP $($deleteResp.StatusCode)), so there is nothing to cross-check on disk."
    }
    Mark-Timing "delete + post-delete checks done"
}
catch {
    Fail "unhandled exception during the run: $_"
}
finally {
    # --- (h) teardown -----------------------------------------------------------
    Step "(h) Teardown"

    if ($apiProcess -and -not $apiProcess.HasExited) {
        Write-Host "Stopping vault-api (PID $($apiProcess.Id)) ..."
        Stop-Process -Id $apiProcess.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
        Pass "vault-api child process stopped"
    }
    elseif ($apiProcess) {
        Info "vault-api process had already exited (exit code $($apiProcess.ExitCode))"
    }

    if ($null -ne $SelectionBackup) {
        Set-Content -Path $SelectionFile -Value $SelectionBackup -NoNewline -Encoding UTF8
        $restored = Get-Content $SelectionFile -Raw
        if ($restored -eq $SelectionBackup) { Pass "SteamPrefill selection file restored to its original content" }
        else { Fail "SteamPrefill selection file restore mismatch: expected '$SelectionBackup', got '$restored'" }
    }

    Info "vault-core nginx (PID $corePid) is LEFT RUNNING on port 80 -- it now supersedes the PoC as the resident cache, as designed by this work package."

    # Cheap hygiene: this run's env vars (incl. the throwaway API key) have
    # no reason to outlive the script -- clear them so a later command in the
    # same PowerShell session/profile can't accidentally inherit them.
    foreach ($varName in @(
        "VAULT_API_KEY", "VAULT_DB_PATH", "VAULT_CACHE_ROOT", "VAULT_LOG_LEVEL",
        "VAULT_STEAMPREFILL_PATH", "VAULT_PREFILL_TIMEOUT_SECONDS",
        "VAULT_WORKER_POLL_SECONDS", "VAULT_SIZE_CACHE_TTL"
    )) {
        Remove-Item "Env:$varName" -ErrorAction SilentlyContinue
    }

    foreach ($f in @($ThrowawayDb, "$ThrowawayDb-wal", "$ThrowawayDb-shm")) {
        if (Test-Path $f) { Remove-Item -Force $f -ErrorAction SilentlyContinue }
    }
    if (-not (Test-Path $ThrowawayDb)) { Pass "throwaway DB removed" }
    else { Fail "throwaway DB could not be removed: $ThrowawayDb" }

    Mark-Timing "teardown complete"

    # --- write RESULTS-<timestamp>.md -------------------------------------------
    $verdict = if ($script:failures.Count -eq 0) { "PASS" } else { "FAIL / BLOCKED" }
    $numbersTable = ($script:numbers.GetEnumerator() | ForEach-Object { "| $($_.Key) | $($_.Value) |" }) -join "`n"
    $timingsTable = ($script:timings.GetEnumerator() | ForEach-Object { "| $($_.Key) | $($_.Value) |" }) -join "`n"
    $failuresList = if ($script:failures.Count -eq 0) { "_none_" } else { ($script:failures | ForEach-Object { "- $_" }) -join "`n" }
    $evidenceBlock = ($script:evidence -join "`n`n")

    $resultsContent = @"
# WP 1.7 -- MVP end-to-end test results

Run: $Timestamp
AppId: $AppId
Verdict: **$verdict**
HEAD at time of run: see ``git log -1`` in the commit this evidence ships with.

## Key numbers

| metric | value |
|---|---|
$numbersTable

## Wall-clock timings (cumulative since script start)

| milestone | elapsed |
|---|---|
$timingsTable

## Failures

$failuresList

## Full request/response evidence (API key redacted)

$evidenceBlock

## Notes

- vault-core nginx (PID $corePid at the time of this run) was left running on
  port 80 at the end of this script -- it now supersedes the Phase-0 PoC
  nginx as the resident cache.
- SteamPrefill's ``Config/selectedAppsToPrefill.json`` was restored to the
  user's own selection after this run (see Teardown above).
- The throwaway SQLite DB used for this run was deleted; it never coexisted
  with any real/persistent vault-api database.
"@
    Set-Content -Path $ResultsFile -Value $resultsContent -Encoding UTF8
    Write-Host ""
    Write-Host "Results written to $ResultsFile" -ForegroundColor Cyan
}

# --- verdict ------------------------------------------------------------------
Write-Host ""
if ($script:failures.Count -eq 0) {
    Write-Host "PASS -- WP 1.7 MVP test verified end-to-end against the real Steam CDN." -ForegroundColor Green
    exit 0
}
else {
    Write-Host "FAIL -- $($script:failures.Count) check(s) failed or the run was blocked:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
