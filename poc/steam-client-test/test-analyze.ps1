<#
.SYNOPSIS
    Proves analyze.ps1's parsing/aggregation logic against a small synthetic
    access.log fixture, before the real Steam-client test ever runs.

.DESCRIPTION
    WP 0.3's actual evidence-gathering run (the real Steam client, a real
    download) happens later, driven by the user following PROTOCOL.md. This
    script instead proves the ANALYSIS SCRIPT itself is correct right now, by
    feeding it a hand-written fixture log in the exact WP 0.1/0.2 "vault" log
    format (poc/conf/nginx.conf log_format) and asserting the computed
    numbers are exactly right.

    The fixture (10 lines) is built to exercise every category the Phase-0
    checkboxes care about:
      - conforming chunk requests (2 hashes, 2 depots: 440 and 441)
      - a conforming manifest request
      - 3 non-conforming URIs (/serverlist/..., /client/..., /health)
      - one explicit Range request and one suffix Range request
      - a first run that's all MISS, a second (later, disjoint-in-time) run
        that's all HIT except one non-cache /health hit
      - per-depot byte/request totals for two different depots

    Every expected number below was computed by hand from the fixture and is
    asserted exactly (not "roughly") - see the comments next to each fixture
    line and each assertion for the derivation.

    Exit code 0 = all assertions passed. 1 = at least one failed (see the
    itemized [FAIL] list).
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir  = $PSScriptRoot
$AnalyzeScript = Join-Path $ScriptDir "analyze.ps1"
$TestTmpDir = Join-Path $ScriptDir "_analyzetest_tmp"
$FixtureLog = Join-Path $TestTmpDir "fixture-access.log"

$script:failures = @()
$script:passCount = 0

function Pass($msg) {
    $script:passCount++
    Write-Host "  [ OK ] $msg" -ForegroundColor Green
}
function Fail($msg) {
    $script:failures += $msg
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}
function Assert-Equal($actual, $expected, $label) {
    # Compares with a small tolerance for doubles, exact for everything else.
    $isMatch = $false
    if ($actual -is [double] -or $expected -is [double]) {
        $a = [double]$actual
        $e = [double]$expected
        $isMatch = [math]::Abs($a - $e) -lt 0.0005
    } else {
        $isMatch = ($actual -eq $expected)
    }
    if ($isMatch) {
        Pass "$label = $actual"
    } else {
        Fail "$label : expected $expected, got $actual"
    }
}

Write-Host "== SteamVault WP 0.3 - analyze.ps1 self-test ==" -ForegroundColor Cyan

if (-not (Test-Path $AnalyzeScript)) {
    Write-Host "[FAIL] analyze.ps1 not found at $AnalyzeScript" -ForegroundColor Red
    exit 1
}

if (Test-Path $TestTmpDir) { Remove-Item -Recurse -Force $TestTmpDir }
New-Item -ItemType Directory -Path $TestTmpDir -Force | Out-Null

# --- build the fixture -------------------------------------------------------
# Two 40-hex-char depot-chunk hashes and one 40-hex-char "different depot"
# hash, built programmatically so there's no risk of a hand-typed hash being
# the wrong length (the conformance regex requires exactly 40 hex chars).
$hashA = "a" * 40   # depot 440, chunk A - requested cold (MISS) then warm (HIT)
$hashB = "b" * 40   # depot 440, chunk B - requested with Range both times
$hashC = "c" * 40   # depot 441, chunk C - warm run only

$fixtureLines = @(
    # --- "first run" (18:00:0x) - all cache MISS ---------------------------
    # 1: depot 440, chunk A, plain GET, MISS, 1,048,576 bytes, upstream 200
    '04/Aug/2026:18:00:00 +0200 uri="/depot/440/chunk/' + $hashA + '" status=200 range="-" upstream_status=200 bytes_sent=1048576 request_time=0.512 cache=MISS'
    # 2: depot 440, chunk B, explicit Range (bytes=0-1023), MISS, 2,097,152 bytes
    #    (mirrors the WP 0.2 finding: upstream ignores Range on miss and
    #    returns the full object anyway - bytes_sent reflects that)
    '04/Aug/2026:18:00:01 +0200 uri="/depot/440/chunk/' + $hashB + '" status=200 range="bytes=0-1023" upstream_status=200 bytes_sent=2097152 request_time=0.734 cache=MISS'
    # 3: depot 440, manifest, MISS, 8,192 bytes
    '04/Aug/2026:18:00:02 +0200 uri="/depot/440/manifest/1234567890123456789/5" status=200 range="-" upstream_status=200 bytes_sent=8192 request_time=0.045 cache=MISS'
    # 4: non-conforming URI #1 - CM server list lookup
    '04/Aug/2026:18:00:03 +0200 uri="/serverlist/440/10" status=200 range="-" upstream_status=200 bytes_sent=512 request_time=0.021 cache=MISS'
    # 5: non-conforming URI #2 - client self-update check
    '04/Aug/2026:18:00:04 +0200 uri="/client/steam_client_ver" status=200 range="-" upstream_status=200 bytes_sent=256 request_time=0.010 cache=MISS'

    # --- "second run" (18:05:0x) - cache warm, all HIT except /health ------
    # 6: depot 440, chunk A, plain GET, HIT, 1,048,576 bytes (same object as #1)
    '04/Aug/2026:18:05:00 +0200 uri="/depot/440/chunk/' + $hashA + '" status=200 range="-" upstream_status=- bytes_sent=1048576 request_time=0.003 cache=HIT'
    # 7: depot 440, chunk B, suffix Range (bytes=-500), HIT, 500 bytes
    '04/Aug/2026:18:05:00 +0200 uri="/depot/440/chunk/' + $hashB + '" status=206 range="bytes=-500" upstream_status=- bytes_sent=500 request_time=0.001 cache=HIT'
    # 8: depot 440, manifest, HIT, 8,192 bytes
    '04/Aug/2026:18:05:01 +0200 uri="/depot/440/manifest/1234567890123456789/5" status=200 range="-" upstream_status=- bytes_sent=8192 request_time=0.001 cache=HIT'
    # 9: depot 441, chunk C, plain GET, HIT, 2,097,152 bytes
    '04/Aug/2026:18:05:01 +0200 uri="/depot/441/chunk/' + $hashC + '" status=200 range="-" upstream_status=- bytes_sent=2097152 request_time=0.002 cache=HIT'
    # 10: /health - outside /depot/, cache status "-" (neither HIT nor MISS)
    '04/Aug/2026:18:05:02 +0200 uri="/health" status=200 range="-" upstream_status=- bytes_sent=3 request_time=0.000 cache=-'
)

$fixtureLines | Set-Content -Path $FixtureLog -Encoding ascii

Write-Host "Fixture written: $FixtureLog ($($fixtureLines.Count) lines)"
Write-Host ""

# --- run 1: whole-log analysis (no -From/-To) --------------------------------
Write-Host "-- Run 1: whole fixture log, no time window --" -ForegroundColor Cyan
$r1 = & $AnalyzeScript -LogFile $FixtureLog -NoReport -PassThru | Select-Object -Last 1
if (-not $r1) {
    Write-Host "[FAIL] analyze.ps1 returned nothing for the whole-log run" -ForegroundColor Red
    exit 1
}

# Parsing sanity
Assert-Equal $r1.Meta.ParsedLines 10 "Meta.ParsedLines"
Assert-Equal $r1.Meta.SkippedLines 0 "Meta.SkippedLines"
Assert-Equal $r1.Meta.WindowLines 10 "Meta.WindowLines"

# 1. URI-scheme conformance: 5 chunk (1,2,6,7,9), 2 manifest (3,8), 3 other (4,5,10)
Assert-Equal $r1.UriConformance.ChunkCount 5 "UriConformance.ChunkCount"
Assert-Equal $r1.UriConformance.ManifestCount 2 "UriConformance.ManifestCount"
Assert-Equal $r1.UriConformance.OtherCount 3 "UriConformance.OtherCount"
Assert-Equal $r1.UriConformance.ChunkPct 50.0 "UriConformance.ChunkPct"
Assert-Equal $r1.UriConformance.ManifestPct 20.0 "UriConformance.ManifestPct"
Assert-Equal $r1.UriConformance.OtherPct 30.0 "UriConformance.OtherPct"

# Non-conforming URI list must be exactly these 3, verbatim, each count=1.
$expectedOther = @("/client/steam_client_ver", "/health", "/serverlist/440/10") | Sort-Object
$actualOther = @($r1.UriConformance.OtherUris | ForEach-Object { $_.Uri }) | Sort-Object
if (($actualOther -join '|') -eq ($expectedOther -join '|')) {
    Pass "UriConformance.OtherUris contains exactly the 3 expected non-conforming URIs"
} else {
    Fail "UriConformance.OtherUris mismatch: expected [$($expectedOther -join ', ')], got [$($actualOther -join ', ')]"
}
if (@($r1.UriConformance.OtherUris | Where-Object { $_.Count -ne 1 }).Count -eq 0) {
    Pass "each non-conforming URI has count=1 (each appears once in the fixture)"
} else {
    Fail "at least one non-conforming URI has an unexpected count"
}

# 2. Range usage: 2 of 10 requests carried a Range header (#2 explicit, #7 suffix)
Assert-Equal $r1.RangeUsage.UsedCount 2 "RangeUsage.UsedCount"
Assert-Equal $r1.RangeUsage.NoneCount 8 "RangeUsage.NoneCount"
Assert-Equal $r1.RangeUsage.ExplicitCount 1 "RangeUsage.ExplicitCount"
Assert-Equal $r1.RangeUsage.SuffixCount 1 "RangeUsage.SuffixCount"
Assert-Equal $r1.RangeUsage.MultiCount 0 "RangeUsage.MultiCount"

# 3. Hit/miss: 4 HIT (6,7,8,9), 5 MISS (1-5), 1 other (10)
Assert-Equal $r1.HitMiss.HitCount 4 "HitMiss.HitCount"
Assert-Equal $r1.HitMiss.MissCount 5 "HitMiss.MissCount"
Assert-Equal $r1.HitMiss.OtherCount 1 "HitMiss.OtherCount"
# hit ratio = 4 / (4+5) * 100 = 44.4444...%
Assert-Equal $r1.HitMiss.HitRatioPct (400.0/9.0) "HitMiss.HitRatioPct"
# hit bytes = 1048576 + 500 + 8192 + 2097152 = 3154420
Assert-Equal $r1.HitMiss.HitBytes 3154420 "HitMiss.HitBytes"
# miss bytes = 1048576 + 2097152 + 8192 + 512 + 256 = 3154688
Assert-Equal $r1.HitMiss.MissBytes 3154688 "HitMiss.MissBytes"
# total bytes = 3154420 + 3154688 + 3 (the /health hit) = 6309111
Assert-Equal $r1.HitMiss.TotalBytes 6309111 "HitMiss.TotalBytes"

# 4. Throughput: wall span 18:00:00 -> 18:05:02 = 302 seconds
Assert-Equal $r1.Throughput.WallSeconds 302 "Throughput.WallSeconds"
Assert-Equal $r1.Throughput.TotalBytes 6309111 "Throughput.TotalBytes"
Assert-Equal $r1.Throughput.ThroughputBps (6309111.0/302.0) "Throughput.ThroughputBps"
# avg MISS request_time = (0.512+0.734+0.045+0.021+0.010)/5 = 1.322/5 = 0.2644
Assert-Equal $r1.Throughput.AvgMissRequestTime 0.2644 "Throughput.AvgMissRequestTime"
# avg HIT request_time = (0.003+0.001+0.001+0.002)/4 = 0.007/4 = 0.00175
Assert-Equal $r1.Throughput.AvgHitRequestTime 0.00175 "Throughput.AvgHitRequestTime"

# 5. Per-depot: depot 440 has 6 requests (1,2,3,6,7,8), 4211188 bytes;
#    depot 441 has 1 request (9), 2097152 bytes. Sorted by bytes desc.
Assert-Equal $r1.PerDepot.Count 2 "PerDepot: number of distinct depots"
$d440 = $r1.PerDepot | Where-Object { $_.DepotId -eq "440" }
$d441 = $r1.PerDepot | Where-Object { $_.DepotId -eq "441" }
if ($d440) {
    Assert-Equal $d440.Requests 6 "PerDepot[440].Requests"
    Assert-Equal $d440.ChunkReqs 4 "PerDepot[440].ChunkReqs"
    Assert-Equal $d440.ManifestReqs 2 "PerDepot[440].ManifestReqs"
    Assert-Equal $d440.Bytes 4211188 "PerDepot[440].Bytes"
} else {
    Fail "PerDepot: depot 440 missing entirely"
}
if ($d441) {
    Assert-Equal $d441.Requests 1 "PerDepot[441].Requests"
    Assert-Equal $d441.ChunkReqs 1 "PerDepot[441].ChunkReqs"
    Assert-Equal $d441.Bytes 2097152 "PerDepot[441].Bytes"
} else {
    Fail "PerDepot: depot 441 missing entirely"
}
if ($r1.PerDepot.Count -ge 2 -and $r1.PerDepot[0].DepotId -eq "440" -and $r1.PerDepot[1].DepotId -eq "441") {
    Pass "PerDepot is sorted by bytes descending (440 before 441 in the whole-log run)"
} else {
    Fail "PerDepot sort order unexpected: $($r1.PerDepot | ForEach-Object { $_.DepotId } | Out-String)"
}

Write-Host ""

# --- run 2: windowed to exactly the "second run" (18:05:00 .. 18:05:02) -----
Write-Host "-- Run 2: -From/-To narrowed to the second-run window --" -ForegroundColor Cyan
$r2 = & $AnalyzeScript -LogFile $FixtureLog -From "2026-08-04 18:05:00" -To "2026-08-04 18:05:02" -NoReport -PassThru | Select-Object -Last 1
if (-not $r2) {
    Write-Host "[FAIL] analyze.ps1 returned nothing for the windowed run" -ForegroundColor Red
    exit 1
}

# Window should contain exactly entries 6,7,8,9,10 = 5 lines
Assert-Equal $r2.Meta.WindowLines 5 "Windowed Meta.WindowLines"
Assert-Equal $r2.UriConformance.ChunkCount 3 "Windowed UriConformance.ChunkCount (6,7,9)"
Assert-Equal $r2.UriConformance.ManifestCount 1 "Windowed UriConformance.ManifestCount (8)"
Assert-Equal $r2.UriConformance.OtherCount 1 "Windowed UriConformance.OtherCount (/health)"

# All 4 cache decisions in the window are HIT, 0 MISS -> hit ratio 100%
Assert-Equal $r2.HitMiss.HitCount 4 "Windowed HitMiss.HitCount"
Assert-Equal $r2.HitMiss.MissCount 0 "Windowed HitMiss.MissCount"
Assert-Equal $r2.HitMiss.HitRatioPct 100.0 "Windowed HitMiss.HitRatioPct"
Assert-Equal $r2.HitMiss.HitBytes 3154420 "Windowed HitMiss.HitBytes"
Assert-Equal $r2.HitMiss.MissBytes 0 "Windowed HitMiss.MissBytes"

# Wall span 18:05:00 -> 18:05:02 = 2 seconds; bytes = 3,154,420 + 3 = 3,154,423
Assert-Equal $r2.Throughput.WallSeconds 2 "Windowed Throughput.WallSeconds"
Assert-Equal $r2.Throughput.TotalBytes 3154423 "Windowed Throughput.TotalBytes"
Assert-Equal $r2.Throughput.ThroughputBps (3154423.0/2.0) "Windowed Throughput.ThroughputBps"

# Per-depot inside the window: depot 441 (2,097,152 bytes) now outranks
# depot 440 (1,057,268 bytes = 1,048,576 + 500 + 8,192), since #1/#2/#3
# (all depot 440, first run) are outside the window.
$w440 = $r2.PerDepot | Where-Object { $_.DepotId -eq "440" }
$w441 = $r2.PerDepot | Where-Object { $_.DepotId -eq "441" }
if ($w440) {
    Assert-Equal $w440.Requests 3 "Windowed PerDepot[440].Requests"
    Assert-Equal $w440.Bytes 1057268 "Windowed PerDepot[440].Bytes"
} else {
    Fail "Windowed PerDepot: depot 440 missing"
}
if ($w441) {
    Assert-Equal $w441.Requests 1 "Windowed PerDepot[441].Requests"
    Assert-Equal $w441.Bytes 2097152 "Windowed PerDepot[441].Bytes"
} else {
    Fail "Windowed PerDepot: depot 441 missing"
}
if ($r2.PerDepot.Count -ge 2 -and $r2.PerDepot[0].DepotId -eq "441") {
    Pass "Windowed PerDepot is sorted by bytes descending (441 before 440 inside this window)"
} else {
    Fail "Windowed PerDepot sort order unexpected"
}

# --- run 3: -NoReport actually skips writing a file; default run writes one -
Write-Host ""
Write-Host "-- Run 3: report-file behavior (-NoReport vs. default) --" -ForegroundColor Cyan
$beforeCount = @(Get-ChildItem -Path $ScriptDir -Filter "RESULTS-*.md" -ErrorAction SilentlyContinue).Count
& $AnalyzeScript -LogFile $FixtureLog -NoReport | Out-Null
$afterNoReportCount = @(Get-ChildItem -Path $ScriptDir -Filter "RESULTS-*.md" -ErrorAction SilentlyContinue).Count
if ($afterNoReportCount -eq $beforeCount) {
    Pass "-NoReport did not write a RESULTS-*.md file"
} else {
    Fail "-NoReport unexpectedly wrote a report file"
}

$TestOutDir = Join-Path $TestTmpDir "reportout"
& $AnalyzeScript -LogFile $FixtureLog -OutDir $TestOutDir | Out-Null
$writtenReports = @(Get-ChildItem -Path $TestOutDir -Filter "RESULTS-*.md" -ErrorAction SilentlyContinue)
if ($writtenReports.Count -eq 1) {
    Pass "default run (no -NoReport) wrote exactly one RESULTS-*.md file to -OutDir"
    $content = Get-Content $writtenReports[0].FullName -Raw
    if ($content -match [regex]::Escape("/client/steam_client_ver") -and $content -match "Hit ratio") {
        Pass "RESULTS-*.md content includes the non-conforming URI and a hit-ratio section (console/file parity)"
    } else {
        Fail "RESULTS-*.md content missing expected sections"
    }
} else {
    Fail "expected exactly 1 RESULTS-*.md file in $TestOutDir, found $($writtenReports.Count)"
}

# --- cleanup ------------------------------------------------------------------
Remove-Item -Recurse -Force $TestTmpDir -ErrorAction SilentlyContinue

# --- verdict --------------------------------------------------------------
Write-Host ""
Write-Host "===================== SUMMARY =====================" -ForegroundColor Cyan
if ($script:failures.Count -eq 0) {
    Write-Host "PASS - $($script:passCount) assertion(s) passed, 0 failed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL - $($script:passCount) passed, $($script:failures.Count) failed:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
