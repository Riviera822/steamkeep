<#
.SYNOPSIS
    Proves verify.ps1's parsing/burst-detection/filesystem-cross-check logic
    against a small synthetic fixture (log + fake cache dir), before any
    real SteamPrefill run happens.

.DESCRIPTION
    Mirrors the approach WP 0.3's test-analyze.ps1 used: a hand-written
    fixture log in the exact WP 0.1/0.2 "vault" log format, with every
    expected number computed by hand and asserted exactly (not "roughly").

    This fixture additionally exercises everything specific to verify.ps1
    that test-analyze.ps1 doesn't cover:
      - burst auto-detection: two widely time-separated groups of log
        lines ("burst A" at 18:00:0x, "burst B" nearly an hour later at
        19:00:0x) - verify.ps1 with no -From/-To must pick burst B (the
        newer one) and ignore burst A entirely.
      - the patch URI scheme (/depot/<id>/patch/<from>/<to>), in addition
        to chunk/manifest.
      - a synthetic poc/cache/depot-shaped fixture directory checked via
        -CacheDepotDir, covering: a depot with a bad (non-40-hex) chunk
        filename, a depot with a stray file directly under its own
        directory (layout violation), a depot with only patch/ content,
        and a depot referenced in the log but ABSENT on disk entirely.
      - the "no SteamPrefill traffic detected" branch (a window containing
        only non-depot URIs).

    Exit code 0 = all assertions passed. 1 = at least one failed (see the
    itemized [FAIL] list).
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir     = $PSScriptRoot
$VerifyScript  = Join-Path $ScriptDir "verify.ps1"
$TestTmpDir    = Join-Path $ScriptDir "_verifytest_tmp"
$FixtureLog    = Join-Path $TestTmpDir "fixture-access.log"
$FixtureCache  = Join-Path $TestTmpDir "fixture-cache\depot"

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

Write-Host "== SteamVault WP 0.4 - verify.ps1 self-test ==" -ForegroundColor Cyan

if (-not (Test-Path $VerifyScript)) {
    Write-Host "[FAIL] verify.ps1 not found at $VerifyScript" -ForegroundColor Red
    exit 1
}

if (Test-Path $TestTmpDir) { Remove-Item -Recurse -Force $TestTmpDir }
New-Item -ItemType Directory -Path $TestTmpDir -Force | Out-Null

# --- build the fixture log ---------------------------------------------------
# Two 40-hex-char depot-chunk hashes per depot, built programmatically so
# there's no risk of a hand-typed hash being the wrong length.
$hashD = "d" * 40   # depot 5001, chunk D - requested cold (MISS) then warm (HIT)
$hashE = "e" * 40   # depot 5001, chunk E - requested with an explicit Range
$hashF = "f" * 40   # depot 5003, chunk F - the depot with NO fixture cache dir
$hash1 = "1" * 40   # depot 9999, burst A only (must be excluded by auto-detect)
$hash2 = "2" * 40   # depot 9999, burst A only

$fixtureLines = @(
    # --- "burst A" (18:00:0x) - must be EXCLUDED from the auto-detected window
    '04/Aug/2026:18:00:00 +0200 uri="/depot/9999/chunk/' + $hash1 + '" status=200 range="-" upstream_status=200 bytes_sent=1000 request_time=0.100 cache=MISS'
    '04/Aug/2026:18:00:01 +0200 uri="/depot/9999/chunk/' + $hash2 + '" status=200 range="-" upstream_status=200 bytes_sent=2000 request_time=0.150 cache=MISS'
    '04/Aug/2026:18:00:02 +0200 uri="/health" status=200 range="-" upstream_status=- bytes_sent=3 request_time=0.000 cache=-'

    # --- "burst B" (19:00:0x, ~58 minutes later) - the "SteamPrefill run" ----
    # B1: depot 5001, chunk D, plain GET, MISS
    '04/Aug/2026:19:00:00 +0200 uri="/depot/5001/chunk/' + $hashD + '" status=200 range="-" upstream_status=200 bytes_sent=500000 request_time=0.400 cache=MISS'
    # B2: depot 5001, chunk E, explicit Range (bytes=0-99), MISS
    '04/Aug/2026:19:00:01 +0200 uri="/depot/5001/chunk/' + $hashE + '" status=206 range="bytes=0-99" upstream_status=206 bytes_sent=600000 request_time=0.500 cache=MISS'
    # B3: depot 5001, manifest, HIT (already cached)
    '04/Aug/2026:19:00:02 +0200 uri="/depot/5001/manifest/111222333/5/444555666" status=200 range="-" upstream_status=- bytes_sent=4096 request_time=0.002 cache=HIT'
    # B4: depot 5002, patch (fromManifest 111 -> toManifest 222), MISS
    '04/Aug/2026:19:00:03 +0200 uri="/depot/5002/patch/111/222" status=200 range="-" upstream_status=200 bytes_sent=70000 request_time=0.300 cache=MISS'
    # B5: depot 5003, chunk F, MISS - this depot has NO corresponding fixture cache dir
    '04/Aug/2026:19:00:04 +0200 uri="/depot/5003/chunk/' + $hashF + '" status=200 range="-" upstream_status=200 bytes_sent=800000 request_time=0.350 cache=MISS'
    # B6: non-conforming URI - the lancache heartbeat probe itself
    '04/Aug/2026:19:00:05 +0200 uri="/lancache-heartbeat" status=404 range="-" upstream_status=- bytes_sent=153 request_time=0.001 cache=-'
    # B7: depot 5001, chunk D again, HIT (warm re-read of the same object as B1)
    '04/Aug/2026:19:00:06 +0200 uri="/depot/5001/chunk/' + $hashD + '" status=200 range="-" upstream_status=- bytes_sent=500000 request_time=0.001 cache=HIT'
)

$fixtureLines | Set-Content -Path $FixtureLog -Encoding ascii

Write-Host "Fixture log written: $FixtureLog ($($fixtureLines.Count) lines)"

# --- build the fixture cache directory ---------------------------------------
# depot 5001: valid chunk d, valid chunk e, ONE bad (non-40-hex) chunk
# filename, a manifest file nested two levels deep, and a stray file
# directly under the depot dir (layout violation, not inside chunk/
# manifest/patch).
$dir5001Chunk    = Join-Path $FixtureCache "5001\chunk"
$dir5001Manifest = Join-Path $FixtureCache "5001\manifest\111222333\5"
New-Item -ItemType Directory -Path $dir5001Chunk -Force | Out-Null
New-Item -ItemType Directory -Path $dir5001Manifest -Force | Out-Null
Set-Content -Path (Join-Path $dir5001Chunk $hashD) -Value ("X" * 10) -NoNewline -Encoding ascii   # 10 bytes
Set-Content -Path (Join-Path $dir5001Chunk $hashE) -Value ("Y" * 10) -NoNewline -Encoding ascii   # 10 bytes
Set-Content -Path (Join-Path $dir5001Chunk "badname.txt") -Value "BADFILE" -NoNewline -Encoding ascii  # 7 bytes - NOT a valid 40-hex name
Set-Content -Path (Join-Path $dir5001Manifest "444555666") -Value ("M" * 20) -NoNewline -Encoding ascii  # 20 bytes
Set-Content -Path (Join-Path $FixtureCache "5001\extra.txt") -Value "EXTRA" -NoNewline -Encoding ascii  # 5 bytes - stray file directly under depot dir

# depot 5002: patch/ only, no chunk/manifest, nothing stray.
$dir5002Patch = Join-Path $FixtureCache "5002\patch\111"
New-Item -ItemType Directory -Path $dir5002Patch -Force | Out-Null
Set-Content -Path (Join-Path $dir5002Patch "222") -Value ("P" * 15) -NoNewline -Encoding ascii  # 15 bytes

# depot 5003: deliberately NOT created on disk at all - the log references
# it (B5), but there is no poc/cache/depot/5003/ directory whatsoever.

Write-Host "Fixture cache dir written: $FixtureCache"
Write-Host ""

# --- run 1: whole fixture log, no -From/-To => auto-detected burst -----------
Write-Host "-- Run 1: whole fixture log, burst auto-detection --" -ForegroundColor Cyan
$r1 = & $VerifyScript -LogFile $FixtureLog -CacheDepotDir $FixtureCache -NoReport -PassThru | Select-Object -Last 1
if (-not $r1) {
    Write-Host "[FAIL] verify.ps1 returned nothing for the whole-log run" -ForegroundColor Red
    exit 1
}

Assert-Equal $r1.Meta.ParsedLines 10 "Meta.ParsedLines"
Assert-Equal $r1.Meta.SkippedLines 0 "Meta.SkippedLines"
Assert-Equal $r1.Meta.WindowMode "auto-burst" "Meta.WindowMode"
Assert-Equal $r1.Meta.BurstInfo.TotalBursts 2 "Meta.BurstInfo.TotalBursts"
Assert-Equal $r1.Meta.BurstInfo.ChosenCount 7 "Meta.BurstInfo.ChosenCount (burst B size)"
Assert-Equal $r1.Meta.WindowLines 7 "Meta.WindowLines"
Assert-Equal $r1.NoPrefillTrafficDetected $false "NoPrefillTrafficDetected"

# 1. URI conformance within burst B: 4 chunk (B1,B2,B5,B7), 1 manifest (B3), 1 patch (B4), 1 other (B6)
Assert-Equal $r1.UriConformance.ChunkCount 4 "UriConformance.ChunkCount"
Assert-Equal $r1.UriConformance.ManifestCount 1 "UriConformance.ManifestCount"
Assert-Equal $r1.UriConformance.PatchCount 1 "UriConformance.PatchCount"
Assert-Equal $r1.UriConformance.OtherCount 1 "UriConformance.OtherCount"
$actualOther = @($r1.UriConformance.OtherUris | ForEach-Object { $_.Uri })
if (($actualOther -join '|') -eq "/lancache-heartbeat") {
    Pass "UriConformance.OtherUris contains exactly [/lancache-heartbeat]"
} else {
    Fail "UriConformance.OtherUris mismatch: got [$($actualOther -join ', ')]"
}

# 2. Range usage: only B2 carries a Range header (explicit)
Assert-Equal $r1.RangeUsage.UsedCount 1 "RangeUsage.UsedCount"
Assert-Equal $r1.RangeUsage.NoneCount 6 "RangeUsage.NoneCount"
Assert-Equal $r1.RangeUsage.ExplicitCount 1 "RangeUsage.ExplicitCount"
Assert-Equal $r1.RangeUsage.SuffixCount 0 "RangeUsage.SuffixCount"
Assert-Equal $r1.RangeUsage.MultiCount 0 "RangeUsage.MultiCount"

# 3. Hit/miss: HIT = B3,B7 (2); MISS = B1,B2,B4,B5 (4); other = B6 (1)
Assert-Equal $r1.HitMiss.HitCount 2 "HitMiss.HitCount"
Assert-Equal $r1.HitMiss.MissCount 4 "HitMiss.MissCount"
Assert-Equal $r1.HitMiss.OtherCount 1 "HitMiss.OtherCount"
# hit ratio = 2 / (2+4) * 100 = 33.333...%
Assert-Equal $r1.HitMiss.HitRatioPct (200.0 / 6.0) "HitMiss.HitRatioPct"
# hit bytes = 4096 (B3) + 500000 (B7) = 504096
Assert-Equal $r1.HitMiss.HitBytes 504096 "HitMiss.HitBytes"
# miss bytes = 500000 + 600000 + 70000 + 800000 = 1970000
Assert-Equal $r1.HitMiss.MissBytes 1970000 "HitMiss.MissBytes"
# total bytes = 504096 + 1970000 + 153 (B6) = 2474249
Assert-Equal $r1.HitMiss.TotalBytes 2474249 "HitMiss.TotalBytes"

# 4. Per-depot: 5001 (B1,B2,B3,B7): 4 req, 3 chunk, 1 manifest, 0 patch, bytes 1604096
#    5002 (B4): 1 req, 0 chunk, 0 manifest, 1 patch, bytes 70000
#    5003 (B5): 1 req, 1 chunk, 0 manifest, 0 patch, bytes 800000
#    sorted by bytes desc: 5001 (1,604,096) > 5003 (800,000) > 5002 (70,000)
Assert-Equal $r1.PerDepot.Count 3 "PerDepot: number of distinct depots in burst B"
$d5001 = $r1.PerDepot | Where-Object { $_.DepotId -eq "5001" }
$d5002 = $r1.PerDepot | Where-Object { $_.DepotId -eq "5002" }
$d5003 = $r1.PerDepot | Where-Object { $_.DepotId -eq "5003" }
Assert-Equal $d5001.Requests 4 "PerDepot[5001].Requests"
Assert-Equal $d5001.ChunkReqs 3 "PerDepot[5001].ChunkReqs"
Assert-Equal $d5001.ManifestReqs 1 "PerDepot[5001].ManifestReqs"
Assert-Equal $d5001.PatchReqs 0 "PerDepot[5001].PatchReqs"
Assert-Equal $d5001.Bytes 1604096 "PerDepot[5001].Bytes"
Assert-Equal $d5002.Requests 1 "PerDepot[5002].Requests"
Assert-Equal $d5002.PatchReqs 1 "PerDepot[5002].PatchReqs"
Assert-Equal $d5002.Bytes 70000 "PerDepot[5002].Bytes"
Assert-Equal $d5003.Requests 1 "PerDepot[5003].Requests"
Assert-Equal $d5003.ChunkReqs 1 "PerDepot[5003].ChunkReqs"
Assert-Equal $d5003.Bytes 800000 "PerDepot[5003].Bytes"
if ($r1.PerDepot[0].DepotId -eq "5001" -and $r1.PerDepot[1].DepotId -eq "5003" -and $r1.PerDepot[2].DepotId -eq "5002") {
    Pass "PerDepot is sorted by bytes descending (5001, 5003, 5002)"
} else {
    Fail "PerDepot sort order unexpected: $($r1.PerDepot | ForEach-Object { $_.DepotId } | Out-String)"
}

# 5. Filesystem check
$f5001 = $r1.FsCheck | Where-Object { $_.DepotId -eq "5001" }
$f5002 = $r1.FsCheck | Where-Object { $_.DepotId -eq "5002" }
$f5003 = $r1.FsCheck | Where-Object { $_.DepotId -eq "5003" }

Assert-Equal $f5001.Found $true "FsCheck[5001].Found"
Assert-Equal $f5001.ChunkCount 3 "FsCheck[5001].ChunkCount (d, e, badname.txt)"
Assert-Equal $f5001.ChunkBytes 27 "FsCheck[5001].ChunkBytes (10+10+7)"
Assert-Equal $f5001.ManifestCount 1 "FsCheck[5001].ManifestCount"
Assert-Equal $f5001.ManifestBytes 20 "FsCheck[5001].ManifestBytes"
Assert-Equal $f5001.PatchCount 0 "FsCheck[5001].PatchCount"
Assert-Equal $f5001.BadChunkNames.Count 1 "FsCheck[5001].BadChunkNames.Count"
Assert-Equal $f5001.UnexpectedEntries.Count 1 "FsCheck[5001].UnexpectedEntries.Count"
if ($f5001.BadChunkNames[0] -match [regex]::Escape("badname.txt")) {
    Pass "FsCheck[5001].BadChunkNames contains badname.txt verbatim"
} else {
    Fail "FsCheck[5001].BadChunkNames did not contain badname.txt: $($f5001.BadChunkNames -join ', ')"
}
if ($f5001.UnexpectedEntries[0] -match [regex]::Escape("extra.txt")) {
    Pass "FsCheck[5001].UnexpectedEntries contains extra.txt verbatim"
} else {
    Fail "FsCheck[5001].UnexpectedEntries did not contain extra.txt: $($f5001.UnexpectedEntries -join ', ')"
}

Assert-Equal $f5002.Found $true "FsCheck[5002].Found"
Assert-Equal $f5002.ChunkCount 0 "FsCheck[5002].ChunkCount"
Assert-Equal $f5002.PatchCount 1 "FsCheck[5002].PatchCount"
Assert-Equal $f5002.PatchBytes 15 "FsCheck[5002].PatchBytes"
Assert-Equal $f5002.BadChunkNames.Count 0 "FsCheck[5002].BadChunkNames.Count"
Assert-Equal $f5002.UnexpectedEntries.Count 0 "FsCheck[5002].UnexpectedEntries.Count"

Assert-Equal $f5003.Found $false "FsCheck[5003].Found (no directory on disk)"

# 6. Layout cross-check aggregate
Assert-Equal $r1.LayoutCrossCheck.Clean $false "LayoutCrossCheck.Clean"
Assert-Equal $r1.LayoutCrossCheck.BadChunkNames.Count 1 "LayoutCrossCheck.BadChunkNames.Count"
Assert-Equal $r1.LayoutCrossCheck.UnexpectedEntries.Count 1 "LayoutCrossCheck.UnexpectedEntries.Count"
Assert-Equal $r1.LayoutCrossCheck.MissingDepotDirs.Count 1 "LayoutCrossCheck.MissingDepotDirs.Count"
Assert-Equal $r1.LayoutCrossCheck.MissingDepotDirs[0] "5003" "LayoutCrossCheck.MissingDepotDirs[0]"

Write-Host ""

# --- run 2: explicit window covering ONLY the /health line in burst A -------
# (no depot-scheme traffic at all in this window) => NoPrefillTrafficDetected
Write-Host "-- Run 2: explicit window with zero depot traffic --" -ForegroundColor Cyan
$r2 = & $VerifyScript -LogFile $FixtureLog -CacheDepotDir $FixtureCache `
    -From "2026-08-04 18:00:02" -To "2026-08-04 18:00:02" -NoReport -PassThru | Select-Object -Last 1

Assert-Equal $r2.Meta.WindowMode "explicit" "Run2 Meta.WindowMode"
Assert-Equal $r2.Meta.WindowLines 1 "Run2 Meta.WindowLines"
Assert-Equal $r2.NoPrefillTrafficDetected $true "Run2 NoPrefillTrafficDetected"
Assert-Equal $r2.UriConformance.ChunkCount 0 "Run2 UriConformance.ChunkCount"
Assert-Equal $r2.UriConformance.OtherCount 1 "Run2 UriConformance.OtherCount"

Write-Host ""

# --- run 3: explicit window covering the WHOLE fixture (both bursts) -------
Write-Host "-- Run 3: explicit window spanning both bursts --" -ForegroundColor Cyan
$r3 = & $VerifyScript -LogFile $FixtureLog -CacheDepotDir $FixtureCache `
    -From "2026-08-04 18:00:00" -To "2026-08-04 19:00:06" -NoReport -PassThru | Select-Object -Last 1

Assert-Equal $r3.Meta.WindowMode "explicit" "Run3 Meta.WindowMode"
Assert-Equal $r3.Meta.WindowLines 10 "Run3 Meta.WindowLines (both bursts)"
# chunk: 2 (burst A) + 4 (burst B) = 6; manifest: 1; patch: 1; other: 2 (/health, /lancache-heartbeat)
Assert-Equal $r3.UriConformance.ChunkCount 6 "Run3 UriConformance.ChunkCount"
Assert-Equal $r3.UriConformance.ManifestCount 1 "Run3 UriConformance.ManifestCount"
Assert-Equal $r3.UriConformance.PatchCount 1 "Run3 UriConformance.PatchCount"
Assert-Equal $r3.UriConformance.OtherCount 2 "Run3 UriConformance.OtherCount"

Write-Host ""

# --- run 4: report-file behavior (-NoReport vs. default) -------------------
Write-Host "-- Run 4: report-file behavior (-NoReport vs. default) --" -ForegroundColor Cyan
$beforeCount = @(Get-ChildItem -Path $ScriptDir -Filter "RESULTS-STEAMPREFILL-*.md" -ErrorAction SilentlyContinue).Count
& $VerifyScript -LogFile $FixtureLog -CacheDepotDir $FixtureCache -NoReport | Out-Null
$afterNoReportCount = @(Get-ChildItem -Path $ScriptDir -Filter "RESULTS-STEAMPREFILL-*.md" -ErrorAction SilentlyContinue).Count
if ($afterNoReportCount -eq $beforeCount) {
    Pass "-NoReport did not write a RESULTS-STEAMPREFILL-*.md file"
} else {
    Fail "-NoReport unexpectedly wrote a report file"
}

$TestOutDir = Join-Path $TestTmpDir "reportout"
& $VerifyScript -LogFile $FixtureLog -CacheDepotDir $FixtureCache -OutDir $TestOutDir | Out-Null
$writtenReports = @(Get-ChildItem -Path $TestOutDir -Filter "RESULTS-STEAMPREFILL-*.md" -ErrorAction SilentlyContinue)
if ($writtenReports.Count -eq 1) {
    Pass "default run (no -NoReport) wrote exactly one RESULTS-STEAMPREFILL-*.md file to -OutDir"
    $content = Get-Content $writtenReports[0].FullName -Raw
    if ($content -match [regex]::Escape("badname.txt") -and $content -match "Path-faithful layout cross-check") {
        Pass "RESULTS-STEAMPREFILL-*.md content includes the bad chunk filename and the layout cross-check section"
    } else {
        Fail "RESULTS-STEAMPREFILL-*.md content missing expected sections"
    }
} else {
    Fail "expected exactly 1 RESULTS-STEAMPREFILL-*.md file in $TestOutDir, found $($writtenReports.Count)"
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
