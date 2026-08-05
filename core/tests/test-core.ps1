<#
.SYNOPSIS
    vault-core (core/nginx/nginx.conf) automated test suite (WP 1.1).

.DESCRIPTION
    Runs core/nginx/nginx.conf natively on Windows (reusing the nginx.exe
    binary already fetched for the Phase-0 PoC at poc/nginx/nginx.exe -- see
    core/README.md for why no separate download/binary lives under core/
    yet) against the REAL Steam CDN, and asserts every ADR-0001 production
    requirement this work package implements:

      1. smoke            - MISS -> store -> HIT, byte-identical (SHA256)
      2. heartbeat         - /lancache-heartbeat: 200 + X-LanCache-Processed-By
      3. range-strip guard - cold object + Range header -> STORED file is
                             still the complete object; documents what the
                             CLIENT received (see console/PASS output)
      4. nocache bypass    - warm object + ?nocache=1 -> upstream re-contacted
                             (log evidence), stored file remains valid
      5. host allowlist    - off-list Host -> rejected (4xx), no open proxy
      6. host pass-through - on-list Host -> proxied to that real edge (200)

    Plus three regressions added after a reviewer-found FAIL on this WP:
      7. B1 retry regression   - a local throwaway two-backend rig (never
                                  the real CDN, which can't be made to fail
                                  on demand) reproduces upstream_status
                                  becoming a comma list ("502, 200") when
                                  proxy_next_upstream retries, and asserts
                                  the eventually-successful body still gets
                                  stored (core/tests/fixtures/retry-regression.conf)
      8. S2 gzip regression    - Accept-Encoding: gzip MISS then HIT, both
                                  byte-identical to ground truth (proves
                                  upstream never sends a raw gzip body that
                                  would land on disk with no Content-Encoding
                                  to say so)
      9. S3 temp-path regression - GET /tmp/proxy/... returns 404, not an
                                  in-flight (or leftover) temp file's bytes

    IMPORTANT -- port 80 contention: a live Steam client may be using the
    Phase-0 PoC's nginx on port 80. This script:
      1. Stops whatever nginx is currently running (poc/stop.ps1 -- safe/
         idempotent if nothing is running).
      2. Starts core/nginx/nginx.conf on port 80 for the duration of the
         test run only.
      3. In a `finally` block (runs even on failure/Ctrl+C): stops the core
         nginx instance, then restarts the PoC's own nginx via
         poc/start.ps1, so the machine is left exactly as this script found
         it -- PoC nginx serving port 80 with poc/conf/nginx.conf, as
         required by the WP 1.1 scope note. poc/ itself is never modified.

    Every /depot/ request in this suite deliberately sends an explicit Host
    header naming a real Steam CDN hostname (see $ValidHost/$PassThroughHost
    below). This mirrors real usage: a DNS- or hosts-file-redirected Steam
    client always sends a genuine *.steamcontent.com/*.steamserver.net Host
    -- curl/Invoke-WebRequest's own default Host (127.0.0.1) is exactly the
    kind of Host the allowlist guard (ADR req 4) is designed to reject, so
    it cannot be used for the positive-path tests here.

.PARAMETER DepotId / ChunkHash
    Known-good live Steam CDN test object, same one used throughout poc/
    (999,232-byte chunk, confirmed reachable since WP 0.1).

.PARAMETER ValidHost
    Host header used for tests that just need *some* legitimate Steam CDN
    Host. "lancache.steamcontent.com" is deliberately chosen here (not just
    for variety): it exercises the ADR req 4 fallback-edge path (this exact
    hostname has no public A record, so vault-core must redirect it to a
    concrete edge internally -- see core/nginx/nginx.conf) on every general
    test in this suite, not just a dedicated one.

.PARAMETER PassThroughHost
    A second, different real Steam CDN edge hostname used specifically to
    prove per-request Host forwarding (ADR req 4) reaches a DIFFERENT edge
    than the fallback, not just the fallback edge every time.

    Exit code 0 = PASS, 1 = FAIL.
#>

[CmdletBinding()]
param(
    [string]$DepotId          = "70403",
    [string]$ChunkHash        = "773d10050d99b2544665873ec2125b3bf273e8b2",
    [string]$BaseUrl          = "http://127.0.0.1",
    [string]$ValidHost        = "lancache.steamcontent.com",
    [string]$PassThroughHost  = "cache2-ams1.steamcontent.com",
    [string]$RejectHost       = "evil.example.com"
)

$ErrorActionPreference = "Stop"

$CoreRoot   = Split-Path $PSScriptRoot -Parent            # .../core
$RepoRoot   = Split-Path $CoreRoot -Parent                # repo root
$PocRoot    = Join-Path $RepoRoot "poc"
$NginxExe   = Join-Path $PocRoot "nginx\nginx.exe"
$CoreConfig = "nginx/nginx.conf"                          # relative to -p $CoreRoot

$LogFile     = Join-Path $CoreRoot "logs\access.log"
$CacheFile   = Join-Path $CoreRoot "cache\depot\$DepotId\chunk\$ChunkHash"
$RequestPath = "/depot/$DepotId/chunk/$ChunkHash"
# Built via concatenation, not string interpolation: PowerShell's expandable
# ("...") string parser treats "$Var?literal" as an attempt to resolve a
# variable literally named "Var?literal" (which doesn't exist -> empty),
# silently swallowing everything up to the next "=" -- confirmed during this
# WP's test-writing. Concatenation (or ${Var} + literal) sidesteps it.
$NocacheRequestPath = $RequestPath + "?nocache=1"
$TestTmpDir  = Join-Path $CoreRoot "_testcore_tmp"

# Synthetic path guaranteed to never be legitimately cached -- used for the
# Host-allowlist rejection test so it can't accidentally be a HIT.
$SyntheticPath = "/depot/1/chunk/0000000000000000000000000000000000000000"
$SyntheticCacheFile = Join-Path $CoreRoot "cache\depot\1\chunk\0000000000000000000000000000000000000000"

$script:failures = @()

function Fail($msg) {
    $script:failures += $msg
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}

function Pass($msg) {
    Write-Host "  [ OK ] $msg" -ForegroundColor Green
}

function Info($msg) {
    Write-Host "  [INFO] $msg" -ForegroundColor Cyan
}

# --- helpers -----------------------------------------------------------------

# Nitpick fixed in review: earlier revisions fell back to
# `Get-Process -Name nginx | Stop-Process -Force`, which force-kills EVERY
# nginx process on the whole host, not just the two instances this script
# manages -- collateral damage for anyone running an unrelated nginx on the
# same machine. These helpers instead read the master PID from each
# instance's own pid file (poc/logs/nginx.pid, core/logs/nginx.pid) and only
# ever touch that specific process tree.
function Get-PidFromFile([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return $null }
    $raw = (Get-Content $PidFile -Raw -ErrorAction SilentlyContinue)
    if (-not $raw) { return $null }
    $parsed = 0
    if ([int]::TryParse($raw.Trim(), [ref]$parsed)) { return $parsed }
    return $null
}

function Stop-NginxInstance([string]$Prefix, [string]$ConfigRelPath, [string]$PidFile) {
    $masterPid = Get-PidFromFile $PidFile
    if (-not $masterPid) { return }
    if (-not (Get-Process -Id $masterPid -ErrorAction SilentlyContinue)) { return }

    # Graceful stop first -- nginx's own -s stop, scoped to exactly this
    # prefix/config, signals the master to shut down its worker(s) cleanly.
    try { & $NginxExe -p "$Prefix" -c $ConfigRelPath -s stop 2>$null } catch {}
    Start-Sleep -Milliseconds 500

    # On Windows, killing just the master does not necessarily take its
    # worker child process(es) down too (no default job-object grouping),
    # so if the master is still alive after a graceful stop, force-kill it
    # AND any of its direct children -- still scoped to this one process
    # tree, never to unrelated nginx processes elsewhere on the host.
    if (Get-Process -Id $masterPid -ErrorAction SilentlyContinue) {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$masterPid" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $masterPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
    }
}

function Stop-AnyNginx {
    Stop-NginxInstance $PocRoot "conf/nginx.conf" (Join-Path $PocRoot "logs\nginx.pid")
    Stop-NginxInstance $CoreRoot $CoreConfig (Join-Path $CoreRoot "logs\nginx.pid")
}

function Start-CoreNginx {
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

    Stop-AnyNginx

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
    Write-Host "vault-core nginx started (PID $corePid)." -ForegroundColor Green
}

function Stop-CoreNginx {
    Stop-NginxInstance $CoreRoot $CoreConfig (Join-Path $CoreRoot "logs\nginx.pid")
}

# Minimal, dependency-free fake HTTP backend for the B1 retry regression
# (test 7 below): a raw TcpListener answering every connection with a fixed
# status/body, run as a background PowerShell job. Avoids relying on
# System.Net.HttpListener (which can require a URL ACL reservation / admin
# rights for some binding forms) and avoids any external dependency (e.g.
# Python) for a test that ships in the repo.
function Start-FakeHttpBackend([int]$Port, [int]$StatusCode, [string]$Body) {
    $statusText = @{200 = "OK"; 502 = "Bad Gateway"}[$StatusCode]
    if (-not $statusText) { $statusText = "Status" }
    return Start-Job -ScriptBlock {
        param($Port, $StatusCode, $StatusText, $Body)
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $bodyBytes = [System.Text.Encoding]::ASCII.GetBytes($Body)
        $headerText = "HTTP/1.1 $StatusCode $StatusText`r`nContent-Length: $($bodyBytes.Length)`r`nConnection: close`r`n`r`n"
        $headerBytes = [System.Text.Encoding]::ASCII.GetBytes($headerText)
        while ($true) {
            $client = $listener.AcceptTcpClient()
            try {
                $stream = $client.GetStream()
                $stream.ReadTimeout = 2000
                $buffer = New-Object byte[] 4096
                try { [void]$stream.Read($buffer, 0, $buffer.Length) } catch {}
                $stream.Write($headerBytes, 0, $headerBytes.Length)
                $stream.Write($bodyBytes, 0, $bodyBytes.Length)
                $stream.Flush()
            }
            finally { $client.Close() }
        }
    } -ArgumentList $Port, $StatusCode, $statusText, $Body
}

function Get-NewLogLines([int]$sinceCount) {
    if (-not (Test-Path $LogFile)) { return @() }
    return Get-Content $LogFile | Select-Object -Skip $sinceCount
}

function Get-LogLineCount {
    if (-not (Test-Path $LogFile)) { return 0 }
    return (Get-Content $LogFile | Measure-Object -Line).Lines
}

# ==============================================================================
Write-Host "== SteamVault vault-core (WP 1.1) test suite ==" -ForegroundColor Cyan
Write-Host "Target object: depot $DepotId chunk $ChunkHash"

try {
    Start-CoreNginx

    # --- /health -------------------------------------------------------------
    try {
        $health = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 10
        if ($health.StatusCode -eq 200) { Pass "/health returns 200" }
        else { Fail "/health returned HTTP $($health.StatusCode), expected 200" }
    }
    catch { Fail "/health request failed: $_" }

    # --- 1. lancache-heartbeat (ADR req 1) ------------------------------------
    Write-Host ""
    Write-Host "-- Test: lancache-heartbeat (ADR req 1) --"
    try {
        $hb = Invoke-WebRequest -Uri "$BaseUrl/lancache-heartbeat" -UseBasicParsing -TimeoutSec 10
        if ($hb.StatusCode -ne 200) {
            Fail "/lancache-heartbeat returned HTTP $($hb.StatusCode), expected 200"
        }
        elseif ($hb.Headers['X-LanCache-Processed-By'] -ne "steamvault") {
            Fail "/lancache-heartbeat X-LanCache-Processed-By was '$($hb.Headers['X-LanCache-Processed-By'])', expected 'steamvault'"
        }
        else {
            Pass "/lancache-heartbeat: 200 + X-LanCache-Processed-By: steamvault"
        }
    }
    catch { Fail "/lancache-heartbeat request failed: $_" }

    # --- 2. smoke: MISS -> store -> HIT, byte-identical -----------------------
    Write-Host ""
    Write-Host "-- Test: smoke (MISS -> store -> HIT) --"
    if (Test-Path $CacheFile) { Remove-Item -Force $CacheFile }
    if (Test-Path $TestTmpDir) { Remove-Item -Recurse -Force $TestTmpDir }
    New-Item -ItemType Directory -Path $TestTmpDir -Force | Out-Null

    $groundTruthHash = $null
    $logBaseline = Get-LogLineCount
    $firstOut = Join-Path $TestTmpDir "first.bin"
    try {
        $r1 = Invoke-WebRequest -Uri "$BaseUrl$RequestPath" -Headers @{Host = $ValidHost} `
            -UseBasicParsing -TimeoutSec 30 -OutFile $firstOut -PassThru
        if ($r1.StatusCode -eq 200) { Pass "first request (cold) returned HTTP 200" }
        else { Fail "first request returned HTTP $($r1.StatusCode), expected 200" }
    }
    catch { Fail "first request threw: $_" }

    if (Test-Path $CacheFile) {
        $groundTruthHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
        Pass "response stored path-faithfully at core/cache/depot/$DepotId/chunk/$ChunkHash (SHA256 $groundTruthHash)"
    }
    else {
        Fail "expected cache file was not created: $CacheFile"
    }

    $secondOut = Join-Path $TestTmpDir "second.bin"
    try {
        $r2 = Invoke-WebRequest -Uri "$BaseUrl$RequestPath" -Headers @{Host = $ValidHost} `
            -UseBasicParsing -TimeoutSec 30 -OutFile $secondOut -PassThru
        if ($r2.StatusCode -eq 200) { Pass "second request (warm) returned HTTP 200" }
        else { Fail "second request returned HTTP $($r2.StatusCode), expected 200" }
    }
    catch { Fail "second request threw: $_" }

    if ((Test-Path $firstOut) -and (Test-Path $secondOut)) {
        $h1 = (Get-FileHash -Algorithm SHA256 -Path $firstOut).Hash
        $h2 = (Get-FileHash -Algorithm SHA256 -Path $secondOut).Hash
        if ($h1 -eq $h2) { Pass "response bodies are byte-identical (SHA256 $h1)" }
        else { Fail "response bodies differ: first=$h1 second=$h2" }
    }
    else {
        Fail "could not compare response bodies - one or both downloads missing"
    }

    # Wrapped in @(...): PowerShell unwraps a single-element pipeline result
    # to a bare scalar string, and $scalar[0] then indexes the string's
    # CHARACTERS, not "element 0 of a one-line array" -- confirmed during
    # this WP's test-writing (a single match silently became the log line's
    # first character, "0", from its leading timestamp digit). @(...) forces
    # array semantics regardless of match count (0, 1, or many).
    $newLines = @((Get-NewLogLines $logBaseline) | Where-Object { $_ -like "*uri=`"$RequestPath`"*" })
    if ($newLines.Count -lt 2) {
        Fail "expected 2 new access log entries for $RequestPath, found $($newLines.Count)"
    }
    else {
        if ($newLines[0] -match "cache=MISS" -and $newLines[0] -match "upstream_status=200") {
            Pass "log: first request cache=MISS, upstream_status=200"
        } else { Fail "log: first request line unexpected: $($newLines[0])" }

        if ($newLines[1] -match "cache=HIT" -and $newLines[1] -match "upstream_status=-") {
            Pass "log: second request cache=HIT, upstream_status=- (no upstream contacted)"
        } else { Fail "log: second request line unexpected: $($newLines[1])" }
    }

    if (-not $groundTruthHash) {
        Fail "no ground-truth hash available -- skipping range/nocache/host tests that depend on it"
    }

    # --- 3. Range-strip guard (ADR req 2) -------------------------------------
    Write-Host ""
    Write-Host "-- Test: Range-strip guard (ADR req 2) --"
    if ($groundTruthHash) {
        if (Test-Path $CacheFile) { Remove-Item -Force $CacheFile }
        Info "cache file removed - object is cold for this test"

        # Invoke-WebRequest cannot set a Range header at all (.NET's
        # WebHeaderCollection treats it as a restricted header, throwing
        # "The 'Range' header must be modified using the appropriate
        # property or method" -- there is no such property exposed by
        # Invoke-WebRequest in Windows PowerShell 5.1). curl.exe has no such
        # restriction and is available on this environment (verified
        # during this WP), so it is used for this one request only.
        $rangeOut = Join-Path $TestTmpDir "range.bin"
        $curlOut = & curl.exe -s -o $rangeOut -w "%{http_code} %{size_download} %{header_json}" `
            -H "Host: $ValidHost" -H "Range: bytes=0-1023" "$BaseUrl$RequestPath"
        $curlParts = $curlOut -split " ", 3
        $statusCode3 = [int]$curlParts[0]
        $clientLen = if (Test-Path $rangeOut) { (Get-Item $rangeOut).Length } else { -1 }
        Info "client received: HTTP $statusCode3, body bytes on disk=$clientLen"
        if ($statusCode3 -eq 200) {
            Pass "client received HTTP 200 (full body) for a Range request on a cold object -- documented consequence of stripping Range upstream (see core/README.md); NOT a 206"
        }
        elseif ($statusCode3 -eq 206) {
            Fail "client received HTTP 206 for a cold-object Range request -- Range was NOT stripped upstream as intended"
        }
        else {
            Fail "client received unexpected HTTP $statusCode3 for the Range request"
        }

        if (Test-Path $CacheFile) {
            $storedHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
            if ($storedHash -eq $groundTruthHash) {
                Pass "stored file after a Range request on a cold object is still the COMPLETE object (SHA256 $storedHash matches ground truth)"
            }
            else {
                Fail "stored file after Range request does NOT match ground truth: got $storedHash, expected $groundTruthHash"
            }
        }
        else {
            Fail "no cache file exists after the Range request -- storage guard may have over-blocked a legitimate 200"
        }
    }
    else {
        Fail "skipped: no ground-truth hash"
    }

    # --- 4. nocache=1 bypass (ADR req 3) ---------------------------------------
    Write-Host ""
    Write-Host "-- Test: nocache=1 bypass (ADR req 3) --"
    if ($groundTruthHash) {
        # Object is warm again after test 3 re-stored it. Confirm that, then
        # bypass it with ?nocache=1 and prove upstream was re-contacted.
        if (-not (Test-Path $CacheFile)) {
            Fail "precondition failed: object should be warm before the nocache test"
        }
        $logBaseline2 = Get-LogLineCount
        $nocacheOut = Join-Path $TestTmpDir "nocache.bin"
        try {
            $r4 = Invoke-WebRequest -Uri "$BaseUrl$NocacheRequestPath" -Headers @{Host = $ValidHost} `
                -UseBasicParsing -TimeoutSec 30 -OutFile $nocacheOut -PassThru
            if ($r4.StatusCode -eq 200) { Pass "?nocache=1 request returned HTTP 200" }
            else { Fail "?nocache=1 request returned HTTP $($r4.StatusCode), expected 200" }
        }
        catch { Fail "?nocache=1 request threw: $_" }

        $nocacheLogPattern = '*uri="' + $NocacheRequestPath + '"*'
        # @(...): see the array-wrapping note on $newLines above -- this is
        # exactly the single-match case that bug bites.
        $nocacheLogLines = @((Get-NewLogLines $logBaseline2) | Where-Object { $_ -like $nocacheLogPattern })
        if ($nocacheLogLines.Count -lt 1) {
            Fail "no access log entry found for the ?nocache=1 request (expected log evidence of upstream contact)"
        }
        elseif ($nocacheLogLines[0] -match "cache=MISS" -and $nocacheLogLines[0] -match "upstream_status=200") {
            Pass "log evidence: ?nocache=1 request shows cache=MISS, upstream_status=200 (upstream was re-contacted, bypassing the stored copy)"
        }
        else {
            Fail "?nocache=1 request log line did not show upstream contact: $($nocacheLogLines[0])"
        }

        if (Test-Path $CacheFile) {
            $postNocacheHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
            if ($postNocacheHash -eq $groundTruthHash) {
                Pass "stored file remains valid after the nocache=1 bypass (SHA256 matches ground truth)"
            }
            else {
                Fail "stored file after nocache=1 bypass does not match ground truth: got $postNocacheHash"
            }
        }
        else {
            Fail "cache file missing after nocache=1 bypass"
        }
    }
    else {
        Fail "skipped: no ground-truth hash"
    }

    # --- 5. Host-header allowlist: reject off-list Host (ADR req 4) ----------
    Write-Host ""
    Write-Host "-- Test: Host-header allowlist rejection (ADR req 4) --"
    if (Test-Path $SyntheticCacheFile) { Remove-Item -Force $SyntheticCacheFile }
    # Windows PowerShell 5.1 (this environment's primary shell) has no
    # -SkipHttpErrorCheck; Invoke-WebRequest throws on any non-2xx status
    # instead, so the expected 4xx here is recovered from the exception.
    try {
        $r5 = Invoke-WebRequest -Uri "$BaseUrl$SyntheticPath" -Headers @{Host = $RejectHost} `
            -UseBasicParsing -TimeoutSec 10
        $code5 = $r5.StatusCode
    }
    catch {
        if ($_.Exception.Response) { $code5 = [int]$_.Exception.Response.StatusCode }
        else { $code5 = $null }
    }
    if ($code5 -ge 400 -and $code5 -lt 500) {
        Pass "off-list Host '$RejectHost' rejected with HTTP $code5 (not proxied -- no open proxy)"
    }
    else {
        Fail "off-list Host '$RejectHost' returned HTTP $code5, expected 4xx"
    }
    if (Test-Path $SyntheticCacheFile) {
        Fail "a file was created for the rejected off-list-Host request -- this must never happen: $SyntheticCacheFile"
    }
    else {
        Pass "no file was stored for the rejected request"
    }

    # --- 6. Host-header pass-through: on-list Host reaches its own edge -------
    Write-Host ""
    Write-Host "-- Test: Host-header pass-through (ADR req 4) --"
    if ($groundTruthHash) {
        $logBaseline3 = Get-LogLineCount
        $passOut = Join-Path $TestTmpDir "passthrough.bin"
        try {
            $r6 = Invoke-WebRequest -Uri "$BaseUrl$NocacheRequestPath" -Headers @{Host = $PassThroughHost} `
                -UseBasicParsing -TimeoutSec 30 -OutFile $passOut -PassThru
            if ($r6.StatusCode -eq 200) { Pass "Host: $PassThroughHost request returned HTTP 200" }
            else { Fail "Host: $PassThroughHost request returned HTTP $($r6.StatusCode), expected 200" }
        }
        catch { Fail "Host: $PassThroughHost request threw: $_" }

        $passLogPattern = '*uri="' + $NocacheRequestPath + '"*'
        # @(...): see the array-wrapping note on $newLines above.
        $passLogLines = @((Get-NewLogLines $logBaseline3) | Where-Object { $_ -like $passLogPattern })
        if ($passLogLines.Count -ge 1 -and $passLogLines[0] -match "upstream_status=200") {
            Pass "log evidence: Host: $PassThroughHost fetch reached its upstream with upstream_status=200"
        }
        else {
            Fail "no log evidence of a successful upstream fetch for Host: $PassThroughHost"
        }

        if (Test-Path $CacheFile) {
            $passHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
            if ($passHash -eq $groundTruthHash) {
                Pass "object re-stored via Host: $PassThroughHost still matches ground truth (SHA256 $passHash)"
            }
            else {
                Fail "object stored via Host: $PassThroughHost does not match ground truth: got $passHash"
            }
        }
        else {
            Fail "cache file missing after Host: $PassThroughHost fetch"
        }
    }
    else {
        Fail "skipped: no ground-truth hash"
    }

    # --- 7. B1 regression: retried 502->200 must still be stored --------------
    Write-Host ""
    Write-Host "-- Test: retry regression, upstream_status='502, 200' must be stored (B1 fix) --"
    $retryFixtureConf = Join-Path $PSScriptRoot "fixtures\retry-regression.conf"
    $retryRigRoot = Join-Path $CoreRoot "_testcore_tmp\retry-rig"
    $badBackendJob = $null
    $okBackendJob = $null
    try {
        foreach ($dir in @(
            (Join-Path $retryRigRoot "cache\depot\t"),
            (Join-Path $retryRigRoot "tmp\client_body"),
            (Join-Path $retryRigRoot "tmp\proxy"),
            (Join-Path $retryRigRoot "tmp\fastcgi"),
            (Join-Path $retryRigRoot "tmp\uwsgi"),
            (Join-Path $retryRigRoot "tmp\scgi"),
            (Join-Path $retryRigRoot "logs")
        )) {
            if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        }

        $badBackendJob = Start-FakeHttpBackend -Port 18101 -StatusCode 502 -Body "BACKEND-1-ALWAYS-502"
        $okBackendJob  = Start-FakeHttpBackend -Port 18102 -StatusCode 200 -Body "BACKEND-2-ALWAYS-200-RETRY-BODY"
        Start-Sleep -Milliseconds 500

        Start-Process -FilePath $NginxExe `
            -ArgumentList @("-p", "`"$retryRigRoot`"", "-c", "`"$retryFixtureConf`"") `
            -WorkingDirectory $retryRigRoot `
            -WindowStyle Hidden
        Start-Sleep -Milliseconds 600

        $retryStoredFile = Join-Path $retryRigRoot "cache\depot\t\retrytest"
        try {
            $r7 = Invoke-WebRequest -Uri "http://127.0.0.1:18096/depot/t/retrytest" -UseBasicParsing -TimeoutSec 15
            if ($r7.StatusCode -eq 200) { Pass "retry-rig request returned HTTP 200 (client got the eventually-successful body)" }
            else { Fail "retry-rig request returned HTTP $($r7.StatusCode), expected 200" }
        }
        catch { Fail "retry-rig request threw: $_" }

        $retryLogFile = Join-Path $retryRigRoot "logs\access.log"
        $retryLogLine = if (Test-Path $retryLogFile) { Get-Content $retryLogFile | Select-Object -Last 1 } else { $null }
        if ($retryLogLine -match "502, 200") {
            Pass "log evidence: upstream_status shows the retry list '502, 200' (reproduces the exact Phase-0 pattern, poc/logs/access.log:3391-3392)"
        }
        else {
            Fail "retry-rig access log did not show the expected '502, 200' retry pattern: $retryLogLine"
        }

        if (Test-Path $retryStoredFile) {
            Pass "B1 FIXED: the retried-then-successful (502, 200) response WAS stored to disk"
        }
        else {
            Fail "B1 REGRESSION: a request that succeeded after a retry (upstream_status='502, 200') was NOT stored -- the map's regex guard is missing or broken"
        }
    }
    finally {
        # Scoped stop: this fixture's own pid file only, never a blanket
        # nginx-process kill (same reasoning as Stop-NginxInstance above).
        Stop-NginxInstance $retryRigRoot "`"$retryFixtureConf`"" (Join-Path $retryRigRoot "logs\nginx.pid")
        if ($badBackendJob) { Stop-Job $badBackendJob -ErrorAction SilentlyContinue; Remove-Job $badBackendJob -Force -ErrorAction SilentlyContinue }
        if ($okBackendJob)  { Stop-Job $okBackendJob  -ErrorAction SilentlyContinue; Remove-Job $okBackendJob  -Force -ErrorAction SilentlyContinue }
    }

    # --- 8. S2 regression: Accept-Encoding: gzip must not corrupt storage ----
    Write-Host ""
    Write-Host "-- Test: Accept-Encoding: gzip MISS then HIT, byte-identical (S2 fix) --"
    if ($groundTruthHash) {
        if (Test-Path $CacheFile) { Remove-Item -Force $CacheFile }
        Info "cache file removed - object is cold for this test"

        # curl.exe, not Invoke-WebRequest: Invoke-WebRequest negotiates and
        # transparently decompresses Accept-Encoding itself, which would
        # mask exactly the bug this test targets (a raw gzip body landing
        # on disk with no Content-Encoding header to say so). curl with an
        # explicit header and no --compressed flag sends the header but
        # does not touch the response body.
        $gzipMissOut = Join-Path $TestTmpDir "gzip_miss.bin"
        $gzipMissCode = & curl.exe -s -o $gzipMissOut -w "%{http_code}" `
            -H "Host: $ValidHost" -H "Accept-Encoding: gzip" "$BaseUrl$RequestPath"
        if ([int]$gzipMissCode -eq 200) { Pass "Accept-Encoding: gzip MISS request returned HTTP 200" }
        else { Fail "Accept-Encoding: gzip MISS request returned HTTP $gzipMissCode, expected 200" }

        $gzipHitOut = Join-Path $TestTmpDir "gzip_hit.bin"
        $gzipHitCode = & curl.exe -s -o $gzipHitOut -w "%{http_code}" `
            -H "Host: $ValidHost" -H "Accept-Encoding: gzip" "$BaseUrl$RequestPath"
        if ([int]$gzipHitCode -eq 200) { Pass "Accept-Encoding: gzip HIT request returned HTTP 200" }
        else { Fail "Accept-Encoding: gzip HIT request returned HTTP $gzipHitCode, expected 200" }

        if ((Test-Path $gzipMissOut) -and (Test-Path $gzipHitOut)) {
            $gzipMissHash = (Get-FileHash -Algorithm SHA256 -Path $gzipMissOut).Hash
            $gzipHitHash  = (Get-FileHash -Algorithm SHA256 -Path $gzipHitOut).Hash
            if ($gzipMissHash -eq $groundTruthHash -and $gzipHitHash -eq $groundTruthHash) {
                Pass "MISS and HIT bodies with Accept-Encoding: gzip both match ground truth (SHA256 $gzipMissHash) -- upstream served identity, not raw gzip"
            }
            else {
                Fail "gzip MISS/HIT bodies do not match ground truth: miss=$gzipMissHash hit=$gzipHitHash expected=$groundTruthHash"
            }
        }
        else {
            Fail "could not compare gzip MISS/HIT bodies - one or both downloads missing"
        }

        if (Test-Path $CacheFile) {
            $gzipStoredHash = (Get-FileHash -Algorithm SHA256 -Path $CacheFile).Hash
            if ($gzipStoredHash -eq $groundTruthHash) {
                Pass "stored file after Accept-Encoding: gzip request matches ground truth (not a raw gzip body)"
            }
            else {
                Fail "stored file after Accept-Encoding: gzip request does NOT match ground truth: got $gzipStoredHash"
            }
        }
        else {
            Fail "no cache file exists after the Accept-Encoding: gzip request"
        }
    }
    else {
        Fail "skipped: no ground-truth hash"
    }

    # --- 9. S3 regression: temp files must not be web-reachable ---------------
    Write-Host ""
    Write-Host "-- Test: GET /tmp/proxy/... is not served (S3 fix) --"
    try {
        $r9 = Invoke-WebRequest -Uri "$BaseUrl/tmp/proxy/whatever" -UseBasicParsing -TimeoutSec 10
        $code9 = $r9.StatusCode
    }
    catch {
        if ($_.Exception.Response) { $code9 = [int]$_.Exception.Response.StatusCode }
        else { $code9 = $null }
    }
    if ($code9 -eq 404) {
        Pass "GET /tmp/proxy/whatever correctly returns 404 (temp files are not web-reachable)"
    }
    else {
        Fail "GET /tmp/proxy/whatever returned HTTP $code9, expected 404"
    }
}
finally {
    # --- teardown: always restore the machine to its pre-test state ----------
    Write-Host ""
    Write-Host "-- Teardown: stopping vault-core nginx, restoring PoC nginx on port 80 --" -ForegroundColor Cyan
    Stop-CoreNginx
    Remove-Item -Recurse -Force $TestTmpDir -ErrorAction SilentlyContinue
    & (Join-Path $PocRoot "start.ps1")
}

# --- verdict ------------------------------------------------------------------
Write-Host ""
if ($script:failures.Count -eq 0) {
    Write-Host "PASS - vault-core (WP 1.1) verified end-to-end against the real Steam CDN." -ForegroundColor Green
    exit 0
}
else {
    Write-Host "FAIL - $($script:failures.Count) check(s) failed:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
