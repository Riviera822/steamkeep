<#
.SYNOPSIS
    Phase 0 PoC - WP 0.2: Range-request test suite against proxy_store.

.DESCRIPTION
    Answers the Phase-0 critical question (docs/PROJECT_PLAN.md section 7/9):
    "Do range requests work cleanly with proxy_store?" This script produces
    EVIDENCE - pass/fail on well-defined correctness checks, and INFO/FINDING
    output for the Steam-CDN-specific behavior questions where "bad news" is
    itself a valid, expected result. See poc/RANGE-FINDINGS.md for the
    write-up derived from a run of this script.

    Scenarios (see docs/PROJECT_PLAN.md section 9 and the WP 0.2 brief):
      A. Baseline: does the upstream honor Range at all, through the proxy,
         on a cold object? (client-visible behavior)
      B. Same request, evaluated from the storage side: what lands in
         poc/cache/ on a cold-cache Range request - nothing, the full file,
         or a corrupt partial file stored as if it were the complete object?
      C. Warm cache + Range request: does nginx's static-file serving path
         (try_files hit, no proxy involved) serve a correct 206?
      D. Warm cache, odd ranges: suffix range, mid-file range, multi-range.
      E. Cold cache + 2 concurrent full downloads: does proxy_store produce
         a correct, non-corrupt stored file under concurrent writers?
      F. Final integrity: stored file still byte-identical to the upstream
         original after everything above.

    Exit code 0 = all well-defined correctness checks (C, D-exactness, E, F)
    passed. This does NOT mean "no risk was found" - scenarios A/B report the
    actual Steam-CDN risk as FINDINGS regardless of exit code; read the
    console output (and RANGE-FINDINGS.md) for the real verdict.

.PARAMETER DepotId
    Steam depot ID to test against. Same default as test-smoke.ps1.

.PARAMETER ChunkHash
    SHA1 chunk hash under that depot. Same default as test-smoke.ps1.

.PARAMETER BaseUrl
    Base URL of the running (or to-be-started) PoC nginx instance.
#>

[CmdletBinding()]
param(
    [string]$DepotId    = "70403",
    [string]$ChunkHash  = "773d10050d99b2544665873ec2125b3bf273e8b2",
    [string]$BaseUrl    = "http://127.0.0.1"
)

$ErrorActionPreference = "Stop"
$PocRoot     = $PSScriptRoot
$LogFile     = Join-Path $PocRoot "logs\access.log"
$ErrorLog    = Join-Path $PocRoot "logs\error.log"
$CacheFile   = Join-Path $PocRoot "cache\depot\$DepotId\chunk\$ChunkHash"
$RequestUri  = "/depot/$DepotId/chunk/$ChunkHash"
$FullUrl     = "$BaseUrl$RequestUri"
$TestTmpDir  = Join-Path $PocRoot "_rangetest_tmp"

$script:failures = @()
$script:findings = @()

function Fail($msg) {
    $script:failures += $msg
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}
function Pass($msg) {
    Write-Host "  [ OK ] $msg" -ForegroundColor Green
}
function Info($msg) {
    Write-Host "  [INFO] $msg" -ForegroundColor Gray
}
function Finding($msg) {
    $script:findings += $msg
    Write-Host "  [FINDING] $msg" -ForegroundColor Yellow
}
function Section($title) {
    Write-Host ""
    Write-Host "== $title ==" -ForegroundColor Cyan
}

# --- helpers -----------------------------------------------------------------

function Invoke-CurlRange {
    <#
        Runs one curl request against the PoC. Returns a hashtable:
        StatusCode, Headers (case-sensitive-first-seen hashtable),
        OutFile, Ok (curl exit code 0).
    #>
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
    $curlExit = $LASTEXITCODE

    $headers = [ordered]@{}
    if (Test-Path $HeaderFile) {
        Get-Content $HeaderFile | ForEach-Object {
            if ($_ -match '^([A-Za-z0-9\-]+):\s*(.*?)\s*$') {
                $headers[$Matches[1]] = $Matches[2]
            }
        }
    }

    return [pscustomobject]@{
        StatusCode = [string]$status
        Headers    = $headers
        OutFile    = $OutFile
        CurlOk     = ($curlExit -eq 0)
        CurlExit   = $curlExit
    }
}

function Clear-CacheEntry {
    if (Test-Path $CacheFile) {
        Remove-Item -Force $CacheFile
        Info "cleared existing cache entry: $CacheFile"
    } else {
        Info "cache entry already absent (cold): $CacheFile"
    }
}

function Get-NewLogLines([int]$sinceCount) {
    if (-not (Test-Path $LogFile)) { return @() }
    return Get-Content $LogFile | Select-Object -Skip $sinceCount |
        Where-Object { $_ -like "*uri=`"$RequestUri`"*" }
}

function Get-LogLineCount {
    if (-not (Test-Path $LogFile)) { return 0 }
    return (Get-Content $LogFile | Measure-Object -Line).Lines
}

function Compare-ByteRange {
    <#
        Compares $len bytes of $file starting at $offset against the same
        slice of $referenceFile. Returns $true if identical.
    #>
    param([string]$File, [string]$ReferenceFile, [long]$Offset, [long]$Len)
    if (-not (Test-Path $File)) { return $false }
    $actual = [System.IO.File]::ReadAllBytes($File)
    if ($actual.LongLength -ne $Len) { return $false }
    $refStream = [System.IO.File]::OpenRead($ReferenceFile)
    try {
        $refStream.Seek($Offset, [System.IO.SeekOrigin]::Begin) | Out-Null
        $expected = New-Object byte[] $Len
        $readTotal = 0
        while ($readTotal -lt $Len) {
            $n = $refStream.Read($expected, $readTotal, $Len - $readTotal)
            if ($n -le 0) { break }
            $readTotal += $n
        }
    } finally {
        $refStream.Close()
    }
    if ($readTotal -ne $Len) { return $false }
    for ($i = 0; $i -lt $Len; $i++) {
        if ($actual[$i] -ne $expected[$i]) { return $false }
    }
    return $true
}

function Sha256Of([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash
}

Write-Host "== SteamVault Phase 0 PoC - Range-request test suite (WP 0.2) ==" -ForegroundColor Cyan
Write-Host "Target: $FullUrl"

# --- 0. nginx running? -------------------------------------------------------
Section "Setup"
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

try {
    $health = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 10
    if ($health.StatusCode -ne 200) { throw "status $($health.StatusCode)" }
    Pass "nginx responds on $BaseUrl/health"
} catch {
    Fail "nginx did not respond on $BaseUrl/health : $_"
    exit 1
}

if (Test-Path $TestTmpDir) { Remove-Item -Recurse -Force $TestTmpDir }
New-Item -ItemType Directory -Path $TestTmpDir -Force | Out-Null

# error.log baseline (to catch new errors caused by this suite only)
$errBaselineCount = 0
if (Test-Path $ErrorLog) { $errBaselineCount = (Get-Content $ErrorLog | Measure-Object -Line).Lines }

# --- 1. Ground truth: clean full (non-Range) fetch through the proxy -------
Section "Ground truth: full object via plain GET (clean cache state first)"
Clear-CacheEntry
$goldenOut = Join-Path $TestTmpDir "golden.bin"
$goldenHdr = Join-Path $TestTmpDir "golden.hdr"
$logCount = Get-LogLineCount
$golden = Invoke-CurlRange -Url $FullUrl -OutFile $goldenOut -HeaderFile $goldenHdr

if (-not $golden.CurlOk -or $golden.StatusCode -ne "200") {
    Fail "ground-truth full GET failed (curl exit=$($golden.CurlExit), http=$($golden.StatusCode)). Chunk may no longer be valid upstream - see README.md for how to override -DepotId/-ChunkHash."
    Remove-Item -Recurse -Force $TestTmpDir -ErrorAction SilentlyContinue
    exit 1
}
$goldenSize = (Get-Item $goldenOut).Length
$goldenHash = Sha256Of $goldenOut
Pass "ground-truth object fetched: $goldenSize bytes, SHA256 $goldenHash"
if (-not (Test-Path $CacheFile)) {
    Fail "ground-truth fetch did not populate the cache file (regression vs WP 0.1?)"
} else {
    Pass "ground-truth fetch stored the object at $CacheFile"
}
$groundTruthLog = Get-NewLogLines $logCount
Info "access.log for ground-truth fetch:"
$groundTruthLog | ForEach-Object { Write-Host "    $_" }

# Reset to cold for the actual Range scenarios below.
Clear-CacheEntry

# --- Scenario A + B: cold cache + Range request -----------------------------
Section "Scenario A/B: cold cache + Range request (bytes=0-1023)"
$rangeAB = "bytes=0-1023"
$abOut = Join-Path $TestTmpDir "ab.bin"
$abHdr = Join-Path $TestTmpDir "ab.hdr"
$logCount = Get-LogLineCount
$ab = Invoke-CurlRange -Url $FullUrl -RangeHeader $rangeAB -OutFile $abOut -HeaderFile $abHdr

if (-not $ab.CurlOk) {
    Fail "scenario A/B request failed at the transport level (curl exit=$($ab.CurlExit))"
} else {
    Pass "request completed (curl transport OK)"
}

# --- Scenario A: client-visible behavior ---
Write-Host ""
Write-Host "-- Scenario A: what does the CLIENT receive? --" -ForegroundColor Cyan
Info "requested: Range: $rangeAB"
Info "received: HTTP $($ab.StatusCode)"
$contentRange = $ab.Headers['Content-Range']
$contentLength = $ab.Headers['Content-Length']
Info "Content-Range: $(if ($contentRange) {$contentRange} else {'(absent)'})"
Info "Content-Length: $(if ($contentLength) {$contentLength} else {'(absent)'})"

if ($ab.StatusCode -eq "206") {
    Finding "upstream (via proxy) HONORS Range on a cold object: HTTP 206, Content-Range='$contentRange'"
} elseif ($ab.StatusCode -eq "200") {
    Finding "upstream (via proxy) IGNORES Range on a cold object: HTTP 200 (full body returned despite Range header)"
} else {
    Finding "unexpected status for cold-cache Range request: HTTP $($ab.StatusCode)"
}

# --- Scenario B: storage-side effect ---
Write-Host ""
Write-Host "-- Scenario B: what LANDS IN THE CACHE? --" -ForegroundColor Cyan
if (-not (Test-Path $CacheFile)) {
    Finding "cold-cache Range request stored NOTHING at $CacheFile (proxy_store did not fire for this response)"
    $verdictB = "nothing-stored"
} else {
    $storedSize = (Get-Item $CacheFile).Length
    $storedHash = Sha256Of $CacheFile
    Info "stored file size: $storedSize bytes (full object is $goldenSize bytes)"
    if ($storedSize -eq $goldenSize -and $storedHash -eq $goldenHash) {
        Finding "cold-cache Range request stored the FULL, CORRECT object ($storedSize bytes, SHA256 matches ground truth) despite the Range header"
        $verdictB = "full-correct"
    } elseif ($storedSize -eq $goldenSize) {
        Finding "cold-cache Range request stored a file of the FULL SIZE ($storedSize bytes) but its content does NOT match the ground-truth object (SHA256 mismatch) - CORRUPT"
        $verdictB = "full-size-corrupt"
    } else {
        # Partial file stored under the full object's path - this is the
        # dangerous case: a truncated file masquerading as the complete object.
        $sliceOk = Compare-ByteRange -File $CacheFile -ReferenceFile $goldenOut -Offset 0 -Len $storedSize
        if ($sliceOk) {
            Finding "cold-cache Range request stored a PARTIAL file ($storedSize of $goldenSize bytes) at the object's canonical path - bytes are a correct prefix slice, but the file is silently TRUNCATED and indistinguishable from a complete cached object to any later plain GET. This is the known proxy_store/Range risk from PROJECT_PLAN.md section 9 materializing."
            $verdictB = "partial-masquerading-as-complete"
        } else {
            Finding "cold-cache Range request stored a PARTIAL, ALSO CORRUPT file ($storedSize of $goldenSize bytes, bytes do not even match the expected prefix)"
            $verdictB = "partial-corrupt"
        }
    }
}
$abLog = Get-NewLogLines $logCount
Info "access.log for scenario A/B:"
$abLog | ForEach-Object { Write-Host "    $_" }

# Verify a plain follow-up GET (no Range) against whatever now sits in the
# cache, to demonstrate the real-world consequence of scenario B.
Write-Host ""
Write-Host "-- Scenario B follow-up: plain GET against the resulting cache state --" -ForegroundColor Cyan
$followOut = Join-Path $TestTmpDir "b_followup.bin"
$followHdr = Join-Path $TestTmpDir "b_followup.hdr"
$logCount = Get-LogLineCount
$follow = Invoke-CurlRange -Url $FullUrl -OutFile $followOut -HeaderFile $followHdr
$followSize = if (Test-Path $followOut) { (Get-Item $followOut).Length } else { -1 }
$followHash = Sha256Of $followOut
Info "plain GET after scenario B returned HTTP $($follow.StatusCode), body $followSize bytes"
if ($verdictB -eq "partial-masquerading-as-complete" -or $verdictB -eq "partial-corrupt" -or $verdictB -eq "full-size-corrupt") {
    if ($followSize -eq $goldenSize -and $followHash -eq $goldenHash) {
        Finding "follow-up plain GET nonetheless returned the correct full object (nginx must have re-fetched from upstream rather than serving the corrupt stored file directly - verify via the log line below)"
    } else {
        Finding "follow-up plain GET returned a CORRUPT/TRUNCATED object to the client ($followSize bytes, expected $goldenSize) - a normal (non-Range) client request would now silently receive bad data from the cache"
    }
}
$followLog = Get-NewLogLines $logCount
Info "access.log for follow-up:"
$followLog | ForEach-Object { Write-Host "    $_" }

# --- Restore a clean, fully-cached object for scenarios C/D -----------------
Section "Re-establish a clean warm cache for scenarios C/D"
Clear-CacheEntry
$logCount = Get-LogLineCount
$warm = Invoke-CurlRange -Url $FullUrl -OutFile (Join-Path $TestTmpDir "warm_seed.bin") -HeaderFile (Join-Path $TestTmpDir "warm_seed.hdr")
if ($warm.StatusCode -ne "200" -or -not (Test-Path $CacheFile) -or (Sha256Of $CacheFile) -ne $goldenHash) {
    Fail "could not re-establish a clean warm cache entry before scenario C/D (status=$($warm.StatusCode))"
} else {
    Pass "warm cache re-seeded correctly (SHA256 matches ground truth)"
}

# --- Scenario C: warm cache + simple Range request --------------------------
Section "Scenario C: warm cache + Range request (bytes=0-1023)"
$cOut = Join-Path $TestTmpDir "c.bin"
$cHdr = Join-Path $TestTmpDir "c.hdr"
$logCount = Get-LogLineCount
$c = Invoke-CurlRange -Url $FullUrl -RangeHeader "bytes=0-1023" -OutFile $cOut -HeaderFile $cHdr

if ($c.StatusCode -eq "206") {
    Pass "warm cache Range request returned HTTP 206"
} else {
    Fail "warm cache Range request returned HTTP $($c.StatusCode), expected 206"
}
$expectedCR = "bytes 0-1023/$goldenSize"
if ($c.Headers['Content-Range'] -eq $expectedCR) {
    Pass "Content-Range correct: $($c.Headers['Content-Range'])"
} else {
    Fail "Content-Range incorrect: got '$($c.Headers['Content-Range'])', expected '$expectedCR'"
}
if ((Test-Path $cOut) -and (Get-Item $cOut).Length -eq 1024 -and (Compare-ByteRange -File $cOut -ReferenceFile $goldenOut -Offset 0 -Len 1024)) {
    Pass "returned 1024 bytes are byte-exact vs. the ground-truth object's first 1024 bytes"
} else {
    Fail "returned bytes do not match the expected slice (offset 0, length 1024)"
}
$cLog = Get-NewLogLines $logCount
if ($cLog -match "cache=HIT" -and $cLog -match "upstream_status=-") {
    Pass "log confirms this was served from disk (cache=HIT, no upstream contact)"
} else {
    Fail "log does not show a clean HIT-path Range response"
}
Info "access.log for scenario C:"
$cLog | ForEach-Object { Write-Host "    $_" }

# --- Scenario D: odd ranges on warm cache -----------------------------------
Section "Scenario D: odd ranges on warm cache"

# D1: suffix range (last 500 bytes)
Write-Host ""
Write-Host "-- D1: suffix range (bytes=-500) --" -ForegroundColor Cyan
$suffixLen = 500
$expectedStart = $goldenSize - $suffixLen
$expectedEnd = $goldenSize - 1
$d1Out = Join-Path $TestTmpDir "d1.bin"
$d1Hdr = Join-Path $TestTmpDir "d1.hdr"
$logCount = Get-LogLineCount
$d1 = Invoke-CurlRange -Url $FullUrl -RangeHeader "bytes=-$suffixLen" -OutFile $d1Out -HeaderFile $d1Hdr
$expectedD1CR = "bytes $expectedStart-$expectedEnd/$goldenSize"
if ($d1.StatusCode -eq "206" -and $d1.Headers['Content-Range'] -eq $expectedD1CR) {
    Pass "suffix range: HTTP 206, Content-Range '$($d1.Headers['Content-Range'])' as expected"
} else {
    Fail "suffix range: HTTP $($d1.StatusCode), Content-Range '$($d1.Headers['Content-Range'])' (expected 206 / '$expectedD1CR')"
}
if ((Test-Path $d1Out) -and (Compare-ByteRange -File $d1Out -ReferenceFile $goldenOut -Offset $expectedStart -Len $suffixLen)) {
    Pass "suffix range bytes are byte-exact vs. ground truth"
} else {
    Fail "suffix range bytes do not match expected slice"
}
Get-NewLogLines $logCount | ForEach-Object { Write-Host "    $_" }

# D2: mid-file range (1000 bytes around the midpoint)
Write-Host ""
Write-Host "-- D2: mid-file range --" -ForegroundColor Cyan
$midStart = [int]([math]::Floor($goldenSize / 2))
$midLen = 1000
$midEnd = $midStart + $midLen - 1
$d2Out = Join-Path $TestTmpDir "d2.bin"
$d2Hdr = Join-Path $TestTmpDir "d2.hdr"
$logCount = Get-LogLineCount
$d2 = Invoke-CurlRange -Url $FullUrl -RangeHeader "bytes=$midStart-$midEnd" -OutFile $d2Out -HeaderFile $d2Hdr
$expectedD2CR = "bytes $midStart-$midEnd/$goldenSize"
if ($d2.StatusCode -eq "206" -and $d2.Headers['Content-Range'] -eq $expectedD2CR) {
    Pass "mid-file range: HTTP 206, Content-Range '$($d2.Headers['Content-Range'])' as expected"
} else {
    Fail "mid-file range: HTTP $($d2.StatusCode), Content-Range '$($d2.Headers['Content-Range'])' (expected 206 / '$expectedD2CR')"
}
if ((Test-Path $d2Out) -and (Compare-ByteRange -File $d2Out -ReferenceFile $goldenOut -Offset $midStart -Len $midLen)) {
    Pass "mid-file range bytes are byte-exact vs. ground truth"
} else {
    Fail "mid-file range bytes do not match expected slice"
}
Get-NewLogLines $logCount | ForEach-Object { Write-Host "    $_" }

# D3: multi-range (record behavior; multipart/byteranges or full body both acceptable)
Write-Host ""
Write-Host "-- D3: multi-range (bytes=0-99,200-299) --" -ForegroundColor Cyan
$d3Out = Join-Path $TestTmpDir "d3.bin"
$d3Hdr = Join-Path $TestTmpDir "d3.hdr"
$logCount = Get-LogLineCount
$d3 = Invoke-CurlRange -Url $FullUrl -RangeHeader "bytes=0-99,200-299" -OutFile $d3Out -HeaderFile $d3Hdr
$d3Size = if (Test-Path $d3Out) { (Get-Item $d3Out).Length } else { -1 }
Info "status: $($d3.StatusCode), Content-Type: $($d3.Headers['Content-Type']), Content-Length: $($d3.Headers['Content-Length']), body size on disk: $d3Size"
if ($d3.StatusCode -eq "206" -and $d3.Headers['Content-Type'] -match "multipart/byteranges") {
    Finding "multi-range request answered as HTTP 206 $($d3.Headers['Content-Type']), body $d3Size bytes"
} elseif ($d3.StatusCode -eq "200") {
    Finding "multi-range request answered as HTTP 200 with the full body ($d3Size bytes) - multi-range collapsed to a full response"
} else {
    Finding "multi-range request answered as HTTP $($d3.StatusCode) (Content-Type: $($d3.Headers['Content-Type']))"
}
Get-NewLogLines $logCount | ForEach-Object { Write-Host "    $_" }

# --- Scenario E: cold cache + 2 concurrent full downloads -------------------
Section "Scenario E: cold cache + 2 concurrent full (non-Range) downloads"
Clear-CacheEntry

$e1Out = Join-Path $TestTmpDir "e1.bin"
$e2Out = Join-Path $TestTmpDir "e2.bin"
$e1StatusFile = Join-Path $TestTmpDir "e1.status"
$e2StatusFile = Join-Path $TestTmpDir "e2.status"

$curlPath = (Get-Command curl.exe).Source
$logCount = Get-LogLineCount

$p1 = Start-Process -FilePath $curlPath `
    -ArgumentList @('-s', '-o', "`"$e1Out`"", '-w', '%{http_code}', "`"$FullUrl`"") `
    -NoNewWindow -PassThru -RedirectStandardOutput $e1StatusFile
$p2 = Start-Process -FilePath $curlPath `
    -ArgumentList @('-s', '-o', "`"$e2Out`"", '-w', '%{http_code}', "`"$FullUrl`"") `
    -NoNewWindow -PassThru -RedirectStandardOutput $e2StatusFile

# Touch .Handle before WaitForExit() - .NET Process quirk: without an early
# Handle access, ExitCode can read back empty even after the process exited.
$p1.Handle | Out-Null
$p2.Handle | Out-Null
$p1.WaitForExit()
$p2.WaitForExit()

$e1Status = if (Test-Path $e1StatusFile) { (Get-Content $e1StatusFile -Raw).Trim() } else { "?" }
$e2Status = if (Test-Path $e2StatusFile) { (Get-Content $e2StatusFile -Raw).Trim() } else { "?" }
Info "concurrent request 1: HTTP $e1Status, process exit code $($p1.ExitCode)"
Info "concurrent request 2: HTTP $e2Status, process exit code $($p2.ExitCode)"

$e1Hash = Sha256Of $e1Out
$e2Hash = Sha256Of $e2Out
if ($e1Status -eq "200" -and $e1Hash -eq $goldenHash) {
    Pass "concurrent request 1 received the correct full object"
} else {
    Fail "concurrent request 1 did not receive the correct object (status=$e1Status, hash=$e1Hash)"
}
if ($e2Status -eq "200" -and $e2Hash -eq $goldenHash) {
    Pass "concurrent request 2 received the correct full object"
} else {
    Fail "concurrent request 2 did not receive the correct object (status=$e2Status, hash=$e2Hash)"
}

if (-not (Test-Path $CacheFile)) {
    Fail "no file was stored in the cache after 2 concurrent full downloads"
} else {
    $eStoredSize = (Get-Item $CacheFile).Length
    $eStoredHash = Sha256Of $CacheFile
    if ($eStoredSize -eq $goldenSize -and $eStoredHash -eq $goldenHash) {
        Pass "final stored file after concurrent downloads is correct: $eStoredSize bytes, SHA256 matches ground truth"
    } else {
        Fail "final stored file after concurrent downloads is CORRUPT/INCOMPLETE: $eStoredSize bytes (expected $goldenSize), SHA256 $eStoredHash (expected $goldenHash)"
        Finding "concurrent proxy_store writers to the same destination file produced a corrupted stored object"
    }
}
Get-NewLogLines $logCount | ForEach-Object { Write-Host "    $_" }

# --- Scenario F: final cache integrity --------------------------------------
Section "Scenario F: final cache integrity check"
if (-not (Test-Path $CacheFile)) {
    Fail "cache file missing at end of suite: $CacheFile"
} else {
    $finalHash = Sha256Of $CacheFile
    $finalSize = (Get-Item $CacheFile).Length
    if ($finalSize -eq $goldenSize -and $finalHash -eq $goldenHash) {
        Pass "stored file at end of suite is byte-identical to the upstream original ($finalSize bytes, SHA256 $finalHash)"
    } else {
        Fail "stored file at end of suite is NOT byte-identical to the upstream original (size $finalSize vs $goldenSize, hash $finalHash vs $goldenHash)"
    }
}

# --- error.log check (informational) ----------------------------------------
Section "error.log (informational)"
if (Test-Path $ErrorLog) {
    $newErrLines = Get-Content $ErrorLog | Select-Object -Skip $errBaselineCount
    if ($newErrLines.Count -eq 0) {
        Info "no new error.log entries during this suite"
    } else {
        Info "$($newErrLines.Count) new error.log entries during this suite:"
        $newErrLines | ForEach-Object { Write-Host "    $_" }
    }
}

# --- cleanup temp download dir (keep the cache + logs) ----------------------
Remove-Item -Recurse -Force $TestTmpDir -ErrorAction SilentlyContinue

# --- summary -----------------------------------------------------------------
Write-Host ""
Write-Host "===================== SUMMARY =====================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Key findings (the actual Phase-0 evidence - read regardless of exit code):" -ForegroundColor Yellow
if ($script:findings.Count -eq 0) {
    Write-Host "  (none recorded)"
} else {
    $script:findings | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
}
Write-Host ""

if ($script:failures.Count -eq 0) {
    Write-Host "PASS - all well-defined correctness checks passed (warm-cache Range serving, concurrent downloads, final integrity)." -ForegroundColor Green
    Write-Host "See findings above / RANGE-FINDINGS.md for the cold-cache Range risk verdict." -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL - $($script:failures.Count) check(s) failed:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
