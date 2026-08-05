<#
.SYNOPSIS
    vault-dns (dns/dnsmasq.conf.template) validation suite (WP 1.8).

.DESCRIPTION
    Renders dns/dnsmasq.conf.template with test placeholder values (plain
    PowerShell string replacement -- no envsubst dependency on the Windows
    side; WP 1.9's container is where the real envsubst substitution lives)
    and validates the result two ways:

      1. syntax   - `dnsmasq --test -C <rendered>` inside WSL2 (dnsmasq 2.92,
                    already installed there by poc/linux-client-test's
                    wsl-setup.sh). This only parses the config; it never
                    binds a socket, so it's always safe regardless of what
                    else is listening on port 53.
      2. functional (default on, -SkipFunctionalTest to disable) - starts a
                    THROWAWAY dnsmasq instance on a non-53 port (5533) via
                    dns/tests/functional-check.sh and, for BOTH an
                    arbitrary synthetic *.steamcontent.com subdomain AND a
                    realistic Steam CDN edge hostname
                    (cache2-ams1.steamcontent.com), confirms:
                      - the A query resolves to the test CACHE_IP (proves
                        address= works, including the wildcard, against a
                        name a real client would actually ask)
                      - the AAAA query comes back NODATA (NOERROR,
                        ANSWER: 0) -- proves the local= pairing required by
                        ADR-0001 requirement 6 actually closes the IPv6
                        bypass, not just "looks right in the file"

    IMPORTANT -- a LIVE dnsmasq instance from Phase 0
    (poc/linux-client-test/scenario-b.sh) may already be running inside
    WSL2, serving 127.0.0.1:53 for a real Linux-client test. This script
    NEVER stops, restarts, or binds over port 53:
      - the syntax check (`dnsmasq --test`) never binds any socket at all
      - the functional check always runs on port 5533 (baked into its own
        rendered config copy, never the primary port=53 rendered file) and
        is killed by exact PID when done, never by name/pattern match
    A safety check before/after the functional test confirms port 53's
    listener count is unchanged, so a regression here is caught rather than
    silently clobbering the live scenario-B instance.

    Exit code 0 = PASS, 1 = FAIL.

.PARAMETER CacheIp
    Test value substituted for ${CACHE_IP}. Deliberately NOT a real address
    on this LAN -- this suite only checks that the template renders and
    behaves correctly, not that any particular production IP is reachable.

.PARAMETER Upstream1 / Upstream2
    Test values substituted for ${UPSTREAM_DNS_1} / ${UPSTREAM_DNS_2} --
    same documented defaults as dns/README.md's placeholder contract table.
#>

[CmdletBinding()]
param(
    [string]$CacheIp      = "10.10.10.50",
    [string]$Upstream1    = "1.1.1.1",
    [string]$Upstream2    = "8.8.8.8",
    [int]$TestPort        = 5533,
    [switch]$SkipFunctionalTest
)

$ErrorActionPreference = "Stop"

$TestsRoot   = $PSScriptRoot                                   # .../dns/tests
$DnsRoot     = Split-Path $TestsRoot -Parent                   # .../dns
$RepoRoot    = Split-Path $DnsRoot -Parent                     # repo root
$TemplatePath = Join-Path $DnsRoot "dnsmasq.conf.template"
$FuncCheckSh  = Join-Path $TestsRoot "functional-check.sh"

$TmpDir           = Join-Path $TestsRoot "_tmp"
$RenderedPath     = Join-Path $TmpDir "rendered-dnsmasq.conf"       # port=53, matches production
$RenderedFuncPath = Join-Path $TmpDir "rendered-dnsmasq-functest.conf"  # port=$TestPort, throwaway-only

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

# Windows path -> WSL2 (/mnt/<drive>/...) path. Built by string manipulation,
# not Resolve-Path, so it also works for paths the caller is about to create
# (Resolve-Path throws on a not-yet-existing file).
function Convert-ToWslPath([string]$WindowsPath) {
    $full = [System.IO.Path]::GetFullPath($WindowsPath)
    if ($full -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Convert-ToWslPath: '$full' does not look like an absolute Windows path"
    }
    $drive = $Matches[1].ToLower()
    $rest  = $Matches[2] -replace '\\', '/'
    return "/mnt/$drive/$rest"
}

function Test-WslDnsmasqAvailable {
    try {
        $out = wsl -u root bash -c "command -v dnsmasq && dnsmasq --version | head -1" 2>&1
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($out)) { return $false }
        Info "WSL dnsmasq found: $($out -join ' ' | Select-Object -First 1)"
        return $true
    }
    catch { return $false }
}

function Get-Port53ListenerCount {
    # Informational safety net only -- counts sockets bound to :53 inside
    # WSL2 so we can assert this script didn't change that number, i.e.
    # never touched the live scenario-B instance (or anything else on 53).
    try {
        $out = wsl -u root bash -c "ss -tuln 2>/dev/null | grep ':53[[:space:]]' | wc -l"
        return [int]($out.Trim())
    }
    catch { return -1 }
}

# ==============================================================================
Write-Host "== SteamVault vault-dns (WP 1.8) template validation suite ==" -ForegroundColor Cyan
Write-Host "Template: $TemplatePath"
Write-Host "Test values: CACHE_IP=$CacheIp UPSTREAM_DNS_1=$Upstream1 UPSTREAM_DNS_2=$Upstream2"

if (-not (Test-Path $TemplatePath)) {
    Fail "template not found: $TemplatePath"
    Write-Host ""
    Write-Host "FAIL - cannot continue without the template." -ForegroundColor Red
    exit 1
}

if (-not (Test-WslDnsmasqAvailable)) {
    Fail "dnsmasq not available inside WSL2 (expected from poc/linux-client-test/wsl-setup.sh). Cannot validate the config without it."
    Write-Host ""
    Write-Host "FAIL - $($script:failures.Count) check(s) failed." -ForegroundColor Red
    exit 1
}

$port53Before = Get-Port53ListenerCount
Info "port 53 listener count before this run: $port53Before (informational baseline)"

New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null

# --- 1. Render the template with test values --------------------------------
Write-Host ""
Write-Host "-- Rendering template with test placeholder values --"
$templateText = Get-Content -Raw -Path $TemplatePath
$rendered = $templateText.
    Replace('${CACHE_IP}', $CacheIp).
    Replace('${UPSTREAM_DNS_1}', $Upstream1).
    Replace('${UPSTREAM_DNS_2}', $Upstream2)

# Only inspect non-comment lines -- the template's own explanatory comments
# use the literal text "${VAR}" to describe the convention (see its header),
# which would otherwise false-positive as an "unsubstituted placeholder".
$nonCommentLines = ($rendered -split "`n") | Where-Object { $_ -notmatch '^\s*#' }
$stillPlaceholder = ($nonCommentLines -join "`n") | Select-String -Pattern '\$\{[A-Z_0-9]+\}'
if ($stillPlaceholder) {
    Fail "rendered config still contains an unsubstituted placeholder outside a comment: $($stillPlaceholder.Matches[0].Value) -- template has a variable this script doesn't know about (update both dns/README.md's placeholder-contract table and this script)"
}
else {
    Pass "all active `${VAR}` placeholders substituted"
}

# Write with LF line endings explicitly -- dnsmasq on Linux is fine with LF;
# avoid depending on PowerShell's default newline behavior across versions.
# NOTE: the -replace expression is parenthesized deliberately -- inside a
# .NET method call's argument list, a bare top-level comma is an argument
# separator, not part of -replace's own (pattern, replacement) operand;
# without the inner parens this silently becomes a 3-argument call and
# WriteAllText fails to resolve (confirmed while writing this script).
[System.IO.File]::WriteAllText($RenderedPath, ($rendered -replace "`r`n", "`n"))
Pass "rendered config written: $RenderedPath"

if ($rendered -match '(?m)^address=/steamcontent\.com/(.+)$' -and $Matches[1] -eq $CacheIp) {
    Pass "address=/steamcontent.com/$CacheIp present (ADR req 6, part 1: A-record rewrite)"
}
else {
    Fail "expected 'address=/steamcontent.com/$CacheIp' not found in rendered config"
}

if ($rendered -match '(?m)^local=/steamcontent\.com/\s*$') {
    Pass "local=/steamcontent.com/ present (ADR req 6, part 2: closes the AAAA/IPv6 bypass -- REQUIRED pairing)"
}
else {
    Fail "expected 'local=/steamcontent.com/' not found in rendered config -- this is the exact IPv6 bypass ADR-0001 req 6 warns about"
}

# --- 2. Syntax check: dnsmasq --test (never binds a socket) -----------------
Write-Host ""
Write-Host "-- Test: dnsmasq --test (syntax check) --"
$wslRenderedPath = Convert-ToWslPath $RenderedPath
try {
    # dnsmasq --test writes its "syntax check OK" line to STDERR (confirmed
    # while writing this script). Under $ErrorActionPreference = "Stop",
    # merging native stderr with 2>&1 makes PowerShell 5.1 wrap each stderr
    # line as a terminating NativeCommandError -- even though dnsmasq's own
    # exit code is 0 -- turning a PASS into a caught exception. Dropping to
    # "Continue" for just this call (and flattening the mixed
    # string/ErrorRecord array via Out-String) avoids that without losing
    # the ability to read the message text.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $testOut = (wsl -u root dnsmasq --test -C $wslRenderedPath 2>&1 | Out-String)
    $ErrorActionPreference = $prevEap

    if ($LASTEXITCODE -eq 0 -and $testOut -match "syntax check OK") {
        Pass "dnsmasq --test: syntax check OK"
    }
    else {
        Fail "dnsmasq --test failed (exit $LASTEXITCODE): $testOut"
    }
}
catch {
    $ErrorActionPreference = $prevEap
    Fail "dnsmasq --test threw: $_"
}

# --- 3. Functional check: throwaway instance on a non-53 port ----------------
if ($SkipFunctionalTest) {
    Write-Host ""
    Info "Skipping functional check (-SkipFunctionalTest given)."
}
else {
    Write-Host ""
    Write-Host "-- Test: functional A/AAAA check (throwaway instance, port $TestPort) --"

    # Separate rendered copy with the port swapped to $TestPort -- the
    # primary $RenderedPath keeps port=53 so the syntax check above reflects
    # the config exactly as it will run in production. This copy is used
    # ONLY for the throwaway instance below and is never used to touch 53.
    $renderedFunc = $rendered -replace '(?m)^port=53\s*$', "port=$TestPort"
    if ($renderedFunc -notmatch "(?m)^port=$TestPort\s*$") {
        Fail "could not rewrite port=53 -> port=$TestPort in the functional-test copy -- template's port directive may have changed shape; skipping the functional check"
    }
    else {
        [System.IO.File]::WriteAllText($RenderedFuncPath, ($renderedFunc -replace "`r`n", "`n"))
        $wslFuncConfPath = Convert-ToWslPath $RenderedFuncPath
        $wslFuncScriptPath = Convert-ToWslPath $FuncCheckSh

        try {
            $funcOut = wsl -u root bash $wslFuncScriptPath $wslFuncConfPath $TestPort 2>&1
            $funcExit = $LASTEXITCODE
            $funcLines = $funcOut -join "`n"

            if ($funcExit -ne 0 -or $funcLines -match "DNSMASQ_START_FAILED") {
                Fail "throwaway dnsmasq instance on port $TestPort failed to start: $funcLines"
            }
            else {
                # Two independent query names, both asserted the same way:
                # an arbitrary synthetic subdomain (proves the wildcard
                # match itself) and a realistic Steam CDN edge hostname
                # (mirrors what a real client actually queries -- same
                # hostname core/tests/test-core.ps1 and
                # poc/linux-client-test/scenario-b.sh use).
                foreach ($case in @(
                    @{ Label = "WILDCARD"; Desc = "an arbitrary *.steamcontent.com name" },
                    @{ Label = "REALNAME"; Desc = "a real Steam CDN edge hostname (cache2-ams1.steamcontent.com)" }
                )) {
                    $label = $case.Label
                    $aResult     = if ($funcLines -match "(?m)^A_RESULT_${label}=(.*)`$")     { $Matches[1].Trim() } else { "" }
                    $aaaaStatus  = if ($funcLines -match "(?m)^AAAA_STATUS_${label}=(.*)`$")  { $Matches[1].Trim() } else { "" }
                    $aaaaAnswers = if ($funcLines -match "(?m)^AAAA_ANSWERS_${label}=(.*)`$") { $Matches[1].Trim() } else { "" }

                    if ($aResult -eq $CacheIp) {
                        Pass "A query for $($case.Desc) -> $aResult (matches test CACHE_IP)"
                    }
                    else {
                        Fail "A query for $($case.Desc) returned '$aResult', expected '$CacheIp'"
                    }

                    if ($aaaaStatus -eq "status: NOERROR" -and $aaaaAnswers -eq "ANSWER: 0") {
                        Pass "AAAA query for $($case.Desc) -> NODATA ($aaaaStatus, $aaaaAnswers) -- IPv6 bypass closed"
                    }
                    else {
                        Fail "AAAA query for $($case.Desc) did not return clean NODATA (got '$aaaaStatus' / '$aaaaAnswers') -- the address=/local= pairing may not be closing the IPv6 bypass (ADR-0001 req 6)"
                    }
                }
            }
        }
        catch {
            Fail "functional check threw: $_"
        }
    }
}

# --- Safety net: confirm port 53 is exactly as we found it -------------------
Write-Host ""
$port53After = Get-Port53ListenerCount
if ($port53Before -ge 0 -and $port53After -eq $port53Before) {
    Pass "port 53 listener count unchanged ($port53After) -- the live scenario-B instance (if any) was never touched"
}
elseif ($port53Before -lt 0 -or $port53After -lt 0) {
    Info "could not verify port 53 listener count (informational check only, not a failure)"
}
else {
    Fail "port 53 listener count changed ($port53Before -> $port53After) -- something on this run affected port 53, investigate before trusting the live scenario-B instance"
}

# --- cleanup ------------------------------------------------------------------
Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue

# --- verdict ------------------------------------------------------------------
Write-Host ""
if ($script:failures.Count -eq 0) {
    Write-Host "PASS - dns/dnsmasq.conf.template renders correctly and enforces the ADR-0001 req 6 AAAA/IPv6-bypass guard." -ForegroundColor Green
    exit 0
}
else {
    Write-Host "FAIL - $($script:failures.Count) check(s) failed:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
