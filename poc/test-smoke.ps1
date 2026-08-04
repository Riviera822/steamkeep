<#
.SYNOPSIS
    Phase 0 PoC smoke test: proves proxy_store hit/miss behavior against a
    real Steam CDN chunk.

.DESCRIPTION
    1. Starts nginx if it isn't already running.
    2. Deletes any previously cached copy of the target depot chunk so this
       run always exercises a real MISS first.
    3. Fetches the chunk through the proxy (expected: MISS, upstream
       contacted, 200, file appears under poc/cache/depot/<id>/chunk/<hash>).
    4. Fetches it again (expected: HIT, served from disk, no upstream call).
    5. Asserts: both responses are 200, the access log shows MISS then HIT
       for these two requests, and the two response bodies are byte-identical.

    Exit code 0 = PASS, 1 = FAIL.

.PARAMETER DepotId
    Steam depot ID to test against. Default is a depot confirmed reachable
    and serving real content while this PoC was built (see README.md).

.PARAMETER ChunkHash
    SHA1 chunk hash under that depot. Default confirmed working alongside
    DepotId above (999232-byte chunk, verified 2026-08-04).
#>

[CmdletBinding()]
param(
    [string]$DepotId    = "70403",
    [string]$ChunkHash  = "773d10050d99b2544665873ec2125b3bf273e8b2",
    [string]$BaseUrl    = "http://127.0.0.1"
)

$ErrorActionPreference = "Stop"
$PocRoot   = $PSScriptRoot
$LogFile   = Join-Path $PocRoot "logs\access.log"
$CacheFile = Join-Path $PocRoot "cache\depot\$DepotId\chunk\$ChunkHash"
$RequestUri = "/depot/$DepotId/chunk/$ChunkHash"
$TestTmpDir = Join-Path $PocRoot "_smoketest_tmp"

$script:failures = @()

function Fail($msg) {
    $script:failures += $msg
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}

function Pass($msg) {
    Write-Host "  [ OK ] $msg" -ForegroundColor Green
}

Write-Host "== SteamVault Phase 0 PoC smoke test ==" -ForegroundColor Cyan
Write-Host "Target: $BaseUrl$RequestUri"

# --- 0. nginx running? ------------------------------------------------------
$proc = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "nginx not running, starting it ..."
    & (Join-Path $PocRoot "start.ps1")
    Start-Sleep -Seconds 1
}
$proc = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
if (-not $proc) {
    Fail "nginx could not be started. See poc/logs/error.log."
    exit 1
}
Pass "nginx is running (PID $($proc.Id -join ', '))"

# health check
try {
    $health = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 10
    if ($health.StatusCode -ne 200) { throw "status $($health.StatusCode)" }
    Pass "nginx responds on $BaseUrl/health"
}
catch {
    Fail "nginx did not respond on $BaseUrl/health : $_"
    exit 1
}

# --- 1. reset: remove any previously cached copy of the test object --------
if (Test-Path $CacheFile) {
    Remove-Item -Force $CacheFile
    Write-Host "Removed pre-existing cache entry: $CacheFile"
}
if (Test-Path $TestTmpDir) { Remove-Item -Recurse -Force $TestTmpDir }
New-Item -ItemType Directory -Path $TestTmpDir -Force | Out-Null

# baseline: how many lines does the access log have before our requests?
$logBaselineCount = 0
if (Test-Path $LogFile) {
    $logBaselineCount = (Get-Content $LogFile | Measure-Object -Line).Lines
}

# --- 2. first request: expect MISS (real upstream fetch + store) -----------
$firstOut = Join-Path $TestTmpDir "first.bin"
try {
    $r1 = Invoke-WebRequest -Uri "$BaseUrl$RequestUri" -UseBasicParsing -TimeoutSec 30 -OutFile $firstOut -PassThru
    if ($r1.StatusCode -eq 200) {
        Pass "first request returned HTTP 200"
    } else {
        Fail "first request returned HTTP $($r1.StatusCode), expected 200"
    }
}
catch {
    Fail "first request threw an error (upstream/network issue?): $_"
    Write-Host ""
    Write-Host "This means either the Steam CDN chunk used as the default test object" -ForegroundColor Yellow
    Write-Host "is no longer valid, or the upstream (dist-fra1.discovery.steamserver.net)" -ForegroundColor Yellow
    Write-Host "is unreachable from this machine. Re-run with -DepotId/-ChunkHash pointing" -ForegroundColor Yellow
    Write-Host "at a known-good depot/chunk pair, see README.md." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "FAIL" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $CacheFile)) {
    Fail "expected cache file was not created: $CacheFile"
} else {
    Pass "response stored path-faithfully at cache/depot/$DepotId/chunk/$ChunkHash"
}

# --- 3. second request: expect HIT (served from disk, no upstream call) ----
$secondOut = Join-Path $TestTmpDir "second.bin"
try {
    $r2 = Invoke-WebRequest -Uri "$BaseUrl$RequestUri" -UseBasicParsing -TimeoutSec 30 -OutFile $secondOut -PassThru
    if ($r2.StatusCode -eq 200) {
        Pass "second request returned HTTP 200"
    } else {
        Fail "second request returned HTTP $($r2.StatusCode), expected 200"
    }
}
catch {
    Fail "second request threw an error: $_"
}

# --- 4. byte-identical bodies ------------------------------------------------
if ((Test-Path $firstOut) -and (Test-Path $secondOut)) {
    $h1 = (Get-FileHash -Algorithm SHA256 -Path $firstOut).Hash
    $h2 = (Get-FileHash -Algorithm SHA256 -Path $secondOut).Hash
    if ($h1 -eq $h2) {
        Pass "response bodies are byte-identical (SHA256 $h1)"
    } else {
        Fail "response bodies differ: first=$h1 second=$h2"
    }
} else {
    Fail "could not compare response bodies - one or both downloads missing"
}

# --- 5. access log shows MISS then HIT for these two requests ---------------
if (-not (Test-Path $LogFile)) {
    Fail "access log not found at $LogFile"
} else {
    $newLines = Get-Content $LogFile | Select-Object -Skip $logBaselineCount |
        Where-Object { $_ -like "*uri=`"$RequestUri`"*" }

    if ($newLines.Count -lt 2) {
        Fail "expected 2 new access log entries for $RequestUri, found $($newLines.Count)"
        $newLines | ForEach-Object { Write-Host "    $_" }
    } else {
        $firstLine  = $newLines[0]
        $secondLine = $newLines[1]

        if ($firstLine -match "cache=MISS" -and $firstLine -match "upstream_status=200") {
            Pass "log: first request marked cache=MISS with upstream_status=200"
        } else {
            Fail "log: first request line did not show MISS+upstream 200: $firstLine"
        }

        if ($secondLine -match "cache=HIT" -and $secondLine -match "upstream_status=-") {
            Pass "log: second request marked cache=HIT, upstream_status=- (no upstream contacted)"
        } else {
            Fail "log: second request line did not show HIT+no-upstream: $secondLine"
        }
    }
}

# --- cleanup temp download dir (keep the cache + logs) ----------------------
Remove-Item -Recurse -Force $TestTmpDir -ErrorAction SilentlyContinue

# --- verdict -----------------------------------------------------------------
Write-Host ""
if ($script:failures.Count -eq 0) {
    Write-Host "PASS - proxy_store hit/miss behavior verified end-to-end." -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL - $($script:failures.Count) check(s) failed:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
