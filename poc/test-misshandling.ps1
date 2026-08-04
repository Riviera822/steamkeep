<#
.SYNOPSIS
    Phase 0 PoC - WP 0.5: miss-handling measurement + correctness suite.
    Synchronous store (nginx.conf) vs. transparent passthrough
    (nginx-passthrough.conf).

.DESCRIPTION
    Answers the docs/PROJECT_PLAN.md section 5/7 "Miss-handling decision":
    on a cache miss, is it better to (A) synchronously fetch-and-store, or
    (B) pass through transparently and let an async prefill fill the cache
    later? This script produces both:

      - Correctness evidence (hard PASS/FAIL) for the passthrough config,
        plus a regression check that the store config still behaves per
        WP 0.1/0.2.
      - Measurement evidence (INFO) - client-perceived latency/throughput
        for cold-miss and warm-HIT full GETs, N iterations each, against
        both configs - written up in MISS-HANDLING-FINDINGS.md.

    The script switches nginx between the two configs itself (stop + start),
    clears the relevant cache entry before every cold-miss measurement, and
    leaves nginx stopped with the test object correctly cached when done.

    Exit code 0 = all correctness checks passed. Measurement numbers are
    informational and do not affect the exit code (there is no "right"
    latency, only evidence) - read the console summary / findings doc.

.PARAMETER DepotId
    Steam depot ID to test against. Same default as test-smoke.ps1/test-range.ps1.

.PARAMETER ChunkHash
    SHA1 chunk hash under that depot. Same default as test-smoke.ps1/test-range.ps1.

.PARAMETER BaseUrl
    Base URL of the PoC nginx instance this script starts/controls.

.PARAMETER Iterations
    Number of repetitions per measurement scenario (cold miss / warm HIT),
    per config. Default 5, per the WP 0.5 brief.
#>

[CmdletBinding()]
param(
    [string]$DepotId     = "70403",
    [string]$ChunkHash   = "773d10050d99b2544665873ec2125b3bf273e8b2",
    [string]$BaseUrl     = "http://127.0.0.1",
    [int]$Iterations     = 5
)

$ErrorActionPreference = "Stop"
$PocRoot      = $PSScriptRoot
$LogFile      = Join-Path $PocRoot "logs\access.log"
$ErrorLog     = Join-Path $PocRoot "logs\error.log"
$CacheFile    = Join-Path $PocRoot "cache\depot\$DepotId\chunk\$ChunkHash"
$CacheDir     = Split-Path $CacheFile -Parent
$RequestUri   = "/depot/$DepotId/chunk/$ChunkHash"
$FullUrl      = "$BaseUrl$RequestUri"
$TestTmpDir   = Join-Path $PocRoot "_misshandlingtest_tmp"
$UpstreamHost = "dist-fra1.discovery.steamserver.net"

# Known-good SHA256 for the default test object (recorded in RANGE-FINDINGS.md,
# 2026-08-04). Only used as an extra cross-check when running with defaults -
# depot chunks are content-addressed (the hash in the URL IS the object's own
# SHA1), so this is a sanity check, not the primary ground truth (that's
# always fetched fresh from upstream below, so custom -DepotId/-ChunkHash
# overrides work correctly too).
$KnownGoodSha256 = "C78FB9F8A88318DD61F318BB95F0B59911C9BBBF8678F6EF2D2724CDBC56A66C"

$script:failures = @()
$script:findings = @()

function Fail($msg)    { $script:failures += $msg; Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Pass($msg)    { Write-Host "  [ OK ] $msg" -ForegroundColor Green }
function Info($msg)    { Write-Host "  [INFO] $msg" -ForegroundColor Gray }
function Finding($msg) { $script:findings += $msg; Write-Host "  [FINDING] $msg" -ForegroundColor Yellow }
function Section($title) { Write-Host ""; Write-Host "== $title ==" -ForegroundColor Cyan }

# --- helpers -----------------------------------------------------------------

function Stop-PocNginx {
    $running = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
    if ($running) {
        & (Join-Path $PocRoot "stop.ps1") | Out-Null
        Start-Sleep -Milliseconds 300
    }
}

function Start-PocNginx([string]$ConfigName) {
    Stop-PocNginx
    Write-Host "Starting nginx with conf/$ConfigName ..."
    & (Join-Path $PocRoot "start.ps1") -Config $ConfigName | Out-Null
    Start-Sleep -Seconds 1
    $proc = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
    if (-not $proc) {
        Fail "nginx could not be started with conf/$ConfigName. See poc/logs/error.log."
        return $false
    }
    try {
        $health = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 10
        if ($health.StatusCode -ne 200) { throw "status $($health.StatusCode)" }
    } catch {
        Fail "nginx (conf/$ConfigName) did not respond on $BaseUrl/health : $_"
        return $false
    }
    Pass "nginx running with conf/$ConfigName (PID $($proc.Id -join ', '))"
    return $true
}

function Clear-CacheEntry {
    if (Test-Path $CacheFile) {
        Remove-Item -Force $CacheFile
        Info "cleared existing cache entry: $CacheFile"
    } else {
        Info "cache entry already absent (cold): $CacheFile"
    }
}

function Seed-CacheEntry([string]$SourceFile) {
    # Simulates what an async prefill job would leave behind: the correct
    # object sitting at its path-faithful location, written by something
    # other than client-triggered proxy traffic.
    if (-not (Test-Path $CacheDir)) {
        New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
    }
    Copy-Item -Force -Path $SourceFile -Destination $CacheFile
}

function Get-LogLineCount {
    if (-not (Test-Path $LogFile)) { return 0 }
    return (Get-Content $LogFile | Measure-Object -Line).Lines
}

function Get-NewLogLines([int]$sinceCount) {
    if (-not (Test-Path $LogFile)) { return @() }
    return Get-Content $LogFile | Select-Object -Skip $sinceCount |
        Where-Object { $_ -like "*uri=`"$RequestUri`"*" }
}

function Sha256Of([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash
}

function Invoke-CurlRange {
    param(
        [string]$Url,
        [string]$RangeHeader = $null,
        [Parameter(Mandatory)][string]$OutFile,
        [Parameter(Mandatory)][string]$HeaderFile
    )
    if (Test-Path $OutFile)    { Remove-Item -Force $OutFile }
    if (Test-Path $HeaderFile) { Remove-Item -Force $HeaderFile }
    $curlArgs = @('-s', '-D', $HeaderFile, '-o', $OutFile, '-w', '%{http_code}')
    if ($RangeHeader) { $curlArgs += @('-H', "Range: $RangeHeader") }
    $curlArgs += $Url
    $status = & curl.exe @curlArgs
    $headers = [ordered]@{}
    if (Test-Path $HeaderFile) {
        Get-Content $HeaderFile | ForEach-Object {
            if ($_ -match '^([A-Za-z0-9\-]+):\s*(.*?)\s*$') { $headers[$Matches[1]] = $Matches[2] }
        }
    }
    return [pscustomobject]@{ StatusCode = [string]$status; Headers = $headers; OutFile = $OutFile }
}

function Invoke-TimedGet {
    <#
        A single client-perceived-latency measurement: curl's own -w timer,
        not PowerShell's (avoids Invoke-WebRequest/.NET overhead skewing
        sub-10ms disk-HIT measurements).
    #>
    param([string]$Url, [string]$OutFile)
    if (Test-Path $OutFile) { Remove-Item -Force $OutFile }
    $raw = & curl.exe -s -o $OutFile -w "%{http_code} %{time_total} %{size_download}" $Url
    $parts = $raw -split '\s+'
    return [pscustomobject]@{
        StatusCode   = $parts[0]
        TimeTotal    = [double]$parts[1]
        SizeDownload = [long]$parts[2]
    }
}

function Get-Stats([double[]]$Values) {
    $sorted = $Values | Sort-Object
    $n = $sorted.Count
    $median = if ($n % 2 -eq 1) { $sorted[[int](($n - 1) / 2)] } else { ($sorted[$n / 2 - 1] + $sorted[$n / 2]) / 2 }
    return [pscustomobject]@{
        Min    = $sorted[0]
        Median = $median
        Max    = $sorted[$n - 1]
    }
}

function Measure-Scenario {
    <#
        Runs $Iterations timed GETs. $PrepStep is invoked before EACH
        iteration (e.g. clear the cache entry for a cold-miss scenario, or a
        no-op for a warm-HIT scenario). Returns per-iteration timings plus
        Get-Stats summaries for latency (s) and throughput (MB/s).
    #>
    param(
        [string]$Label,
        [scriptblock]$PrepStep
    )
    $timings = @()
    $out = Join-Path $TestTmpDir "measure.bin"
    for ($i = 1; $i -le $Iterations; $i++) {
        & $PrepStep
        $r = Invoke-TimedGet -Url $FullUrl -OutFile $out
        if ($r.StatusCode -ne "200") {
            Fail "$Label iteration $i : unexpected HTTP $($r.StatusCode)"
            continue
        }
        $mbps = if ($r.TimeTotal -gt 0) { ($r.SizeDownload / 1MB) / $r.TimeTotal } else { 0 }
        $timings += [pscustomobject]@{ Iteration = $i; Seconds = $r.TimeTotal; Bytes = $r.SizeDownload; MBps = $mbps }
        Info "$Label iteration $i : $(Format-Num ($r.TimeTotal * 1000) 2) ms, $($r.SizeDownload) bytes, $(Format-Num $mbps 2) MB/s"
    }
    $latStats = Get-Stats ($timings | ForEach-Object { $_.Seconds })
    $thrStats = Get-Stats ($timings | ForEach-Object { $_.MBps })
    return [pscustomobject]@{
        Label            = $Label
        Timings          = $timings
        LatencySeconds   = $latStats
        ThroughputMBps   = $thrStats
    }
}

function Format-Num([double]$Value, [int]$Decimals) {
    # Force invariant (dot-decimal) formatting regardless of the host's
    # current culture - avoids "288,51" showing up on de-DE etc. systems.
    return $Value.ToString("F$Decimals", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Format-ScenarioResult($r) {
    $minMs = Format-Num ($r.LatencySeconds.Min * 1000) 2
    $medMs = Format-Num ($r.LatencySeconds.Median * 1000) 2
    $maxMs = Format-Num ($r.LatencySeconds.Max * 1000) 2
    $minThr = Format-Num $r.ThroughputMBps.Min 2
    $medThr = Format-Num $r.ThroughputMBps.Median 2
    $maxThr = Format-Num $r.ThroughputMBps.Max 2
    return "{0,-32} latency ms  min={1,7} median={2,7} max={3,7}  |  throughput MB/s  min={4,6} median={5,6} max={6,6}" -f `
        $r.Label, $minMs, $medMs, $maxMs, $minThr, $medThr, $maxThr
}

Write-Host "== SteamVault Phase 0 PoC - Miss-handling measurement suite (WP 0.5) ==" -ForegroundColor Cyan
Write-Host "Target: $FullUrl"
Write-Host "Iterations per scenario: $Iterations"

if (Test-Path $TestTmpDir) { Remove-Item -Recurse -Force $TestTmpDir }
New-Item -ItemType Directory -Path $TestTmpDir -Force | Out-Null

$errBaselineCount = 0
if (Test-Path $ErrorLog) { $errBaselineCount = (Get-Content $ErrorLog | Measure-Object -Line).Lines }

# --- 0. Ground truth: fetch directly from the upstream CDN edge, bypassing
#        BOTH nginx configs entirely, so this reference is independent of
#        whichever config is under test. ---------------------------------
Section "Ground truth (direct upstream fetch, bypassing nginx)"
$groundTruthOut = Join-Path $TestTmpDir "groundtruth.bin"
$gt = Invoke-CurlRange -Url "http://$UpstreamHost$RequestUri" -OutFile $groundTruthOut -HeaderFile (Join-Path $TestTmpDir "groundtruth.hdr")
if ($gt.StatusCode -ne "200" -or -not (Test-Path $groundTruthOut)) {
    Fail "could not fetch ground-truth object directly from $UpstreamHost (http=$($gt.StatusCode)). Chunk may no longer be valid - see README.md for -DepotId/-ChunkHash override."
    Remove-Item -Recurse -Force $TestTmpDir -ErrorAction SilentlyContinue
    exit 1
}
$goldenSize = (Get-Item $groundTruthOut).Length
$goldenHash = Sha256Of $groundTruthOut
Pass "ground truth fetched directly from upstream: $goldenSize bytes, SHA256 $goldenHash"
if ($DepotId -eq "70403" -and $ChunkHash -eq "773d10050d99b2544665873ec2125b3bf273e8b2") {
    if ($goldenHash -eq $KnownGoodSha256) {
        Pass "ground truth matches the known-good hash recorded in RANGE-FINDINGS.md"
    } else {
        Finding "ground truth SHA256 ($goldenHash) differs from the previously recorded known-good hash ($KnownGoodSha256) - upstream object may have changed"
    }
}

# =============================================================================
# PHASE A - Correctness: passthrough config (nginx-passthrough.conf)
# =============================================================================
Section "PHASE A: Correctness - passthrough config"
if (-not (Start-PocNginx "nginx-passthrough.conf")) { exit 1 }

# A1: cold miss -> client gets the correct full body, nothing stored.
Write-Host ""
Write-Host "-- A1: cold miss, full GET --" -ForegroundColor Cyan
Clear-CacheEntry
$logCount = Get-LogLineCount
$a1Out = Join-Path $TestTmpDir "a1.bin"
$a1 = Invoke-CurlRange -Url $FullUrl -OutFile $a1Out -HeaderFile (Join-Path $TestTmpDir "a1.hdr")
if ($a1.StatusCode -eq "200") { Pass "cold miss returned HTTP 200" } else { Fail "cold miss returned HTTP $($a1.StatusCode), expected 200" }
$a1Hash = Sha256Of $a1Out
if ($a1Hash -eq $goldenHash) { Pass "client body is byte-identical to ground truth (SHA256 $a1Hash)" } else { Fail "client body does NOT match ground truth (got $a1Hash, expected $goldenHash)" }
if (Test-Path $CacheFile) {
    Fail "passthrough MISS stored a file at $CacheFile - it should store NOTHING"
} else {
    Pass "nothing stored on disk after passthrough cold miss (as designed)"
}
$a1Log = Get-NewLogLines $logCount
if ($a1Log -match "cache=MISS" -and $a1Log -match "upstream_status=200") {
    Pass "log confirms MISS + real upstream contact"
} else {
    Fail "log does not show the expected MISS/upstream_status=200 line: $a1Log"
}

# A2: pre-seeded file (simulating a prefill fill) -> HIT, byte-identical.
Write-Host ""
Write-Host "-- A2: pre-seeded cache entry -> HIT --" -ForegroundColor Cyan
Clear-CacheEntry
Seed-CacheEntry -SourceFile $groundTruthOut
$logCount = Get-LogLineCount
$a2Out = Join-Path $TestTmpDir "a2.bin"
$a2 = Invoke-CurlRange -Url $FullUrl -OutFile $a2Out -HeaderFile (Join-Path $TestTmpDir "a2.hdr")
if ($a2.StatusCode -eq "200") { Pass "pre-seeded request returned HTTP 200" } else { Fail "pre-seeded request returned HTTP $($a2.StatusCode), expected 200" }
$a2Hash = Sha256Of $a2Out
if ($a2Hash -eq $goldenHash) { Pass "served body is byte-identical to the seeded/ground-truth object" } else { Fail "served body does NOT match (got $a2Hash, expected $goldenHash)" }
$a2Log = Get-NewLogLines $logCount
if ($a2Log -match "cache=HIT" -and $a2Log -match "upstream_status=-") {
    Pass "log confirms a prefill-seeded file is served as a HIT with no upstream contact (this is the whole point of prefill-first)"
} else {
    Fail "log does not show the expected HIT/upstream_status=- line: $a2Log"
}

# A3: Range request on a cold miss -> forwarded 1:1, nothing stored.
Write-Host ""
Write-Host "-- A3: cold miss + Range request --" -ForegroundColor Cyan
Clear-CacheEntry
$logCount = Get-LogLineCount
$a3Out = Join-Path $TestTmpDir "a3.bin"
$a3 = Invoke-CurlRange -Url $FullUrl -RangeHeader "bytes=0-1023" -OutFile $a3Out -HeaderFile (Join-Path $TestTmpDir "a3.hdr")
$a3Size = if (Test-Path $a3Out) { (Get-Item $a3Out).Length } else { -1 }
Info "client received: HTTP $($a3.StatusCode), Content-Range=$($a3.Headers['Content-Range']), body $a3Size bytes"
Finding "passthrough cold-miss Range request: client received HTTP $($a3.StatusCode) (Content-Range: $(if ($a3.Headers['Content-Range']) {$a3.Headers['Content-Range']} else {'absent'})) - forwarded from upstream unmodified, whatever upstream chose to do with the Range header"
if (Test-Path $CacheFile) {
    Fail "passthrough Range-on-miss stored a file at $CacheFile - it should store NOTHING, regardless of the Range header"
} else {
    Pass "nothing stored on disk after passthrough cold-miss Range request (as designed - see nginx-passthrough.conf comments on why Range is not stripped here)"
}

Clear-CacheEntry

# =============================================================================
# PHASE B - Correctness regression: store config (nginx.conf, WP 0.1/0.2)
# =============================================================================
Section "PHASE B: Correctness regression - store config (nginx.conf)"
if (-not (Start-PocNginx "nginx.conf")) { exit 1 }

Write-Host ""
Write-Host "-- B1: cold miss -> stored on disk (WP 0.1 expectation) --" -ForegroundColor Cyan
Clear-CacheEntry
$logCount = Get-LogLineCount
$b1Out = Join-Path $TestTmpDir "b1.bin"
$b1 = Invoke-CurlRange -Url $FullUrl -OutFile $b1Out -HeaderFile (Join-Path $TestTmpDir "b1.hdr")
if ($b1.StatusCode -eq "200") { Pass "cold miss returned HTTP 200" } else { Fail "cold miss returned HTTP $($b1.StatusCode), expected 200" }
if (-not (Test-Path $CacheFile)) {
    Fail "store-mode cold miss did not create $CacheFile - regression vs WP 0.1"
} else {
    $storedHash = Sha256Of $CacheFile
    if ($storedHash -eq $goldenHash) { Pass "stored file matches ground truth (SHA256 $storedHash)" } else { Fail "stored file does NOT match ground truth (got $storedHash)" }
}
$b1Log = Get-NewLogLines $logCount
if ($b1Log -match "cache=MISS" -and $b1Log -match "upstream_status=200") { Pass "log confirms MISS + upstream 200" } else { Fail "log does not show expected MISS line: $b1Log" }

Write-Host ""
Write-Host "-- B2: warm cache -> HIT, served from disk (WP 0.1 expectation) --" -ForegroundColor Cyan
$logCount = Get-LogLineCount
$b2Out = Join-Path $TestTmpDir "b2.bin"
$b2 = Invoke-CurlRange -Url $FullUrl -OutFile $b2Out -HeaderFile (Join-Path $TestTmpDir "b2.hdr")
if ($b2.StatusCode -eq "200") { Pass "warm request returned HTTP 200" } else { Fail "warm request returned HTTP $($b2.StatusCode), expected 200" }
$b2Hash = Sha256Of $b2Out
if ($b2Hash -eq $goldenHash) { Pass "served body byte-identical to ground truth" } else { Fail "served body does NOT match ground truth (got $b2Hash)" }
$b2Log = Get-NewLogLines $logCount
if ($b2Log -match "cache=HIT" -and $b2Log -match "upstream_status=-") { Pass "log confirms HIT, no upstream contact" } else { Fail "log does not show expected HIT line: $b2Log" }
Info "full WP 0.2 Range-request regression suite (suffix/mid/multi-range, concurrency) is covered separately by test-range.ps1 - not duplicated here."

# =============================================================================
# PHASE C - Measurement: passthrough config
# =============================================================================
Section "PHASE C: Measurement - passthrough config ($Iterations iterations/scenario)"
if (-not (Start-PocNginx "nginx-passthrough.conf")) { exit 1 }

Write-Host ""
Write-Host "-- C1: cold miss, full GET x$Iterations (cache cleared before each) --" -ForegroundColor Cyan
$ptColdMiss = Measure-Scenario -Label "passthrough cold-miss" -PrepStep { Clear-CacheEntry }

Write-Host ""
Write-Host "-- C2: warm HIT, full GET x$Iterations (cache seeded once, kept warm) --" -ForegroundColor Cyan
Clear-CacheEntry
Seed-CacheEntry -SourceFile $groundTruthOut
$ptWarmHit = Measure-Scenario -Label "passthrough warm-HIT" -PrepStep { } # no-op: stays warm across iterations

# =============================================================================
# PHASE D - Measurement: store config
# =============================================================================
Section "PHASE D: Measurement - store config ($Iterations iterations/scenario)"
if (-not (Start-PocNginx "nginx.conf")) { exit 1 }

Write-Host ""
Write-Host "-- D1: cold miss, full GET x$Iterations (cache cleared before each) --" -ForegroundColor Cyan
$stColdMiss = Measure-Scenario -Label "store cold-miss" -PrepStep { Clear-CacheEntry }

Write-Host ""
Write-Host "-- D2: warm HIT, full GET x$Iterations (cache stays populated from D1's last iteration) --" -ForegroundColor Cyan
if (-not (Test-Path $CacheFile) -or (Sha256Of $CacheFile) -ne $goldenHash) {
    # Re-seed defensively in case D1's last iteration somehow left it cold/corrupt.
    Clear-CacheEntry
    $seedOut = Join-Path $TestTmpDir "d_seed.bin"
    Invoke-CurlRange -Url $FullUrl -OutFile $seedOut -HeaderFile (Join-Path $TestTmpDir "d_seed.hdr") | Out-Null
}
$stWarmHit = Measure-Scenario -Label "store warm-HIT" -PrepStep { } # no-op: stays warm across iterations

# =============================================================================
# SUMMARY
# =============================================================================
Section "MEASUREMENT SUMMARY"
Write-Host ""
Write-Host (Format-ScenarioResult $ptColdMiss)
Write-Host (Format-ScenarioResult $ptWarmHit)
Write-Host (Format-ScenarioResult $stColdMiss)
Write-Host (Format-ScenarioResult $stWarmHit)

Write-Host ""
Write-Host "-- Derived comparisons --" -ForegroundColor Cyan

# Store-mode overhead on the miss path vs passthrough (median latency).
$missOverheadAbsMs = ($stColdMiss.LatencySeconds.Median - $ptColdMiss.LatencySeconds.Median) * 1000
$missOverheadPct = if ($ptColdMiss.LatencySeconds.Median -gt 0) {
    (($stColdMiss.LatencySeconds.Median - $ptColdMiss.LatencySeconds.Median) / $ptColdMiss.LatencySeconds.Median) * 100
} else { 0 }
$overheadMsg = "store-mode miss-path overhead vs passthrough (median): {0} ms ({1}%)" -f `
    (Format-Num $missOverheadAbsMs 2), (Format-Num $missOverheadPct 1)
Write-Host "  $overheadMsg"
$script:findings += $overheadMsg

# HIT vs MISS speedup factor, per config.
$ptSpeedup = if ($ptWarmHit.LatencySeconds.Median -gt 0) { $ptColdMiss.LatencySeconds.Median / $ptWarmHit.LatencySeconds.Median } else { 0 }
$stSpeedup = if ($stWarmHit.LatencySeconds.Median -gt 0) { $stColdMiss.LatencySeconds.Median / $stWarmHit.LatencySeconds.Median } else { 0 }
$ptSpeedupMsg = "passthrough: HIT is {0}x faster than MISS (median)" -f (Format-Num $ptSpeedup 1)
$stSpeedupMsg = "store: HIT is {0}x faster than MISS (median)" -f (Format-Num $stSpeedup 1)
Write-Host "  $ptSpeedupMsg"
Write-Host "  $stSpeedupMsg"
$script:findings += $ptSpeedupMsg
$script:findings += $stSpeedupMsg

# =============================================================================
# CLEANUP - leave nginx stopped, cache correct and warm (matches repo's
# pre-existing state: the known-good chunk cached and verified).
# =============================================================================
Section "Cleanup"
Stop-PocNginx
Pass "nginx stopped"

if ((Test-Path $CacheFile) -and (Sha256Of $CacheFile) -eq $goldenHash) {
    Pass "cache left in a correct, warm state ($CacheFile matches ground truth)"
} else {
    Seed-CacheEntry -SourceFile $groundTruthOut
    if ((Sha256Of $CacheFile) -eq $goldenHash) {
        Pass "cache re-seeded to a correct, warm state before exit"
    } else {
        Fail "could not leave the cache in a correct state at $CacheFile"
    }
}

if (Test-Path $ErrorLog) {
    $newErrLines = Get-Content $ErrorLog | Select-Object -Skip $errBaselineCount
    if ($newErrLines.Count -gt 0) {
        Info "$($newErrLines.Count) new error.log entries during this suite:"
        $newErrLines | ForEach-Object { Write-Host "    $_" }
    }
}

Remove-Item -Recurse -Force $TestTmpDir -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "===================== SUMMARY =====================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Key findings (measurement + notable behavior - read regardless of exit code):" -ForegroundColor Yellow
$script:findings | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
Write-Host ""

if ($script:failures.Count -eq 0) {
    Write-Host "PASS - all correctness checks passed (passthrough store-nothing behavior, HIT-on-seed, store-mode regression)." -ForegroundColor Green
    Write-Host "See MEASUREMENT SUMMARY above / MISS-HANDLING-FINDINGS.md for the numbers." -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL - $($script:failures.Count) check(s) failed:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
