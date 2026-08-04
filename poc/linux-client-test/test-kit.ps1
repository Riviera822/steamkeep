<#
.SYNOPSIS
    WP 0.6 self-test: proves everything about this kit that CAN be proven
    today, on plain Windows/PowerShell, without WSL2 existing yet.

.DESCRIPTION
    WSL2 is not available on this machine as of the writing of this kit
    (docs/PROJECT_PLAN.md section 7 / poc/linux-client-test/PROTOCOL.md
    section 0) - none of wsl-setup.sh, scenario-a.sh, scenario-b.sh can
    actually be RUN here. This script instead checks everything that is
    testable right now:

      1. Bash syntax check (`bash -n`) of wsl-setup.sh, scenario-a.sh,
         scenario-b.sh, using git-bash's bash.exe if present on PATH.
         Catches typos/quoting errors that would otherwise only surface
         the first time a human runs these scripts for real inside WSL2.
      2. LF-only line endings on all three .sh files (they run inside
         Linux - a stray CRLF is a real failure mode there, not cosmetic;
         see poc/.gitattributes for the enforcement side of this).
      3. PowerShell parse-check of analyze-windows.ps1 (catches syntax
         errors independent of ever executing it).
      4. Functional check of analyze-windows.ps1 against a synthetic
         access-log fixture (same fixture-log approach as WP 0.3's
         test-analyze.ps1) covering BOTH the zero-traffic path (Scenario A
         reads "0 requests -> OK") and the with-traffic path (Scenario B
         reads "N requests, M of them HIT"), plus the two "unexpected"
         cross-checks (Scenario A sees traffic; Scenario B sees none).

    Exit code 0 = all checks passed. 1 = at least one failed (see the
    itemized [FAIL] list).

.EXAMPLE
    .\test-kit.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$KitDir = $PSScriptRoot
$script:failures = @()
$script:passCount = 0
$script:skipCount = 0

function Pass($msg) {
    $script:passCount++
    Write-Host "  [ OK ] $msg" -ForegroundColor Green
}
function Fail($msg) {
    $script:failures += $msg
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}
function Skip($msg) {
    $script:skipCount++
    Write-Host "  [SKIP] $msg" -ForegroundColor Yellow
}

function Find-GitBash {
    <#
        Deliberately does NOT just take the first "bash.exe" found via
        Get-Command: on a machine with WSL partially set up (the Windows
        feature enabled but no distro installed yet - exactly this
        machine's state per PROTOCOL.md section 0), Windows registers a
        "bash.exe" launcher stub under
        %LOCALAPPDATA%\Microsoft\WindowsApps\ that, when run, tries to
        launch WSL and fails ("no installed distributions") instead of
        being a real bash - which would make every syntax check below fail
        for a reason that has nothing to do with these scripts. Git for
        Windows' real bash.exe is usually NOT even on PowerShell's PATH
        (only Git\cmd and Git\mingw64\bin typically are), so it's found by
        checking well-known install locations directly, falling back to
        any PATH match that ISN'T the WindowsApps stub.
    #>
    $candidates = @(
        (Join-Path $env:ProgramFiles "Git\bin\bash.exe"),
        (Join-Path $env:ProgramFiles "Git\usr\bin\bash.exe")
    )
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Git\bin\bash.exe")
    }
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }

    $onPath = Get-Command bash.exe -All -ErrorAction SilentlyContinue
    foreach ($cmd in $onPath) {
        if ($cmd.Source -notmatch 'WindowsApps') { return $cmd.Source }
    }
    return $null
}

Write-Host "== SteamVault WP 0.6 - Linux-client test kit self-test ==" -ForegroundColor Cyan
Write-Host "(WSL2 is not available on this machine yet - see PROTOCOL.md section 0." -ForegroundColor Cyan
Write-Host " This only proves what's provable without it.)" -ForegroundColor Cyan
Write-Host ""

$ShellScripts = @("wsl-setup.sh", "scenario-a.sh", "scenario-b.sh")

# =============================================================================
# 1 + 2. Shell scripts: existence, LF-only line endings, bash syntax check
# =============================================================================
Write-Host "-- 1/2: shell script line endings + bash syntax --" -ForegroundColor Cyan

$bashPath = Find-GitBash
if (-not $bashPath) {
    Skip "no real bash.exe found (checked Git for Windows' well-known install paths and PATH, excluding the WindowsApps WSL-launcher stub) - cannot syntax-check .sh files today"
} else {
    Write-Host "  (using bash: $bashPath)" -ForegroundColor Gray
}

foreach ($name in $ShellScripts) {
    $path = Join-Path $KitDir $name
    if (-not (Test-Path $path)) {
        Fail "$name not found at $path"
        continue
    }

    # --- LF-only check: read raw bytes, fail on any CR (0x0D) ---------------
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $crCount = ($bytes | Where-Object { $_ -eq 0x0D }).Count
    if ($crCount -eq 0) {
        Pass "$name has LF-only line endings (0 CR bytes)"
    } else {
        Fail "$name contains $crCount CR byte(s) - must be LF-only (it runs inside Linux/WSL2). Check poc/.gitattributes and the editor/tool that touched this file."
    }

    # --- bash -n syntax check ------------------------------------------------
    if ($bashPath) {
        $bashOutput = & $bashPath -n $path 2>&1
        if ($LASTEXITCODE -eq 0) {
            Pass "$name passes 'bash -n' syntax check"
        } else {
            Fail "$name failed 'bash -n': $bashOutput"
        }
    }
}
Write-Host ""

# =============================================================================
# 3. PowerShell parse-check of analyze-windows.ps1
# =============================================================================
Write-Host "-- 3: analyze-windows.ps1 parse check --" -ForegroundColor Cyan

$AnalyzeWindowsScript = Join-Path $KitDir "analyze-windows.ps1"
if (-not (Test-Path $AnalyzeWindowsScript)) {
    Fail "analyze-windows.ps1 not found at $AnalyzeWindowsScript"
} else {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($AnalyzeWindowsScript, [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count -eq 0) {
        Pass "analyze-windows.ps1 parses with 0 syntax errors"
    } else {
        Fail "analyze-windows.ps1 has $($parseErrors.Count) parse error(s): $($parseErrors -join '; ')"
    }
}

$AnalyzeScript = Join-Path $KitDir "..\steam-client-test\analyze.ps1"
if (-not (Test-Path $AnalyzeScript)) {
    Fail "poc/steam-client-test/analyze.ps1 not found at $AnalyzeScript - analyze-windows.ps1 has nothing to wrap without it"
} else {
    Pass "poc/steam-client-test/analyze.ps1 found (the engine analyze-windows.ps1 wraps)"
}
Write-Host ""

# =============================================================================
# 4. Functional check against a synthetic access-log fixture
# =============================================================================
Write-Host "-- 4: analyze-windows.ps1 functional check (synthetic fixture) --" -ForegroundColor Cyan

if (-not (Test-Path $AnalyzeWindowsScript) -or -not (Test-Path $AnalyzeScript)) {
    Skip "skipping functional checks - a prerequisite script is missing (see failures above)"
} else {
    $TestTmpDir = Join-Path $KitDir "_testkit_tmp"
    if (Test-Path $TestTmpDir) { Remove-Item -Recurse -Force $TestTmpDir }
    New-Item -ItemType Directory -Path $TestTmpDir -Force | Out-Null
    $FixtureLog = Join-Path $TestTmpDir "fixture-access.log"

    # Two 40-hex-char depot-chunk hashes, built programmatically so there's
    # no risk of a hand-typed hash being the wrong length (same convention
    # as WP 0.3's test-analyze.ps1 fixture).
    $hashX = "1" * 40
    $hashY = "2" * 40

    # "Busy window" 18:00:00 - 18:00:02: one MISS, one HIT, one manifest HIT -
    # stands in for a Scenario-B run where traffic reached the cache and a
    # (previously-warmed) object came back HIT.
    # "Empty window" 18:10:00 - 18:10:05: no lines at all - stands in for
    # a clean Scenario-A null result.
    $fixtureLines = @(
        '10/Aug/2026:18:00:00 +0200 uri="/depot/999/chunk/' + $hashX + '" status=200 range="-" upstream_status=200 bytes_sent=1048576 request_time=0.400 cache=MISS'
        '10/Aug/2026:18:00:01 +0200 uri="/depot/999/chunk/' + $hashY + '" status=200 range="-" upstream_status=- bytes_sent=524288 request_time=0.002 cache=HIT'
        '10/Aug/2026:18:00:02 +0200 uri="/depot/999/manifest/1111111111111111111/1" status=200 range="-" upstream_status=- bytes_sent=8192 request_time=0.001 cache=HIT'
    )
    $fixtureLines | Set-Content -Path $FixtureLog -Encoding ascii

    Write-Host "Fixture written: $FixtureLog ($($fixtureLines.Count) lines, busy window 18:00:00-18:00:02)"

    $busyFrom = "2026-08-10 18:00:00"
    $busyTo   = "2026-08-10 18:00:02"
    $emptyFrom = "2026-08-10 18:10:00"
    $emptyTo   = "2026-08-10 18:10:05"

    # --- 4a. Scenario A against the EMPTY window -> expect the "[ OK ]" null-result path
    $out = & $AnalyzeWindowsScript -Scenario A -From $emptyFrom -To $emptyTo -LogFile $FixtureLog -OutDir $TestTmpDir -NoReport *>&1 | Out-String
    if ($out -match '\[ OK \] 0 requests in window - the expected null result') {
        Pass "Scenario A / empty window -> reports the expected null-result OK line"
    } else {
        Fail "Scenario A / empty window did not report the expected OK line. Output:`n$out"
    }

    # --- 4b. Scenario A against the BUSY window -> expect the "[NOTE]" contradiction path
    $out = & $AnalyzeWindowsScript -Scenario A -From $busyFrom -To $busyTo -LogFile $FixtureLog -OutDir $TestTmpDir -NoReport *>&1 | Out-String
    if ($out -match '\[NOTE\] 3 request\(s\) found in this window') {
        Pass "Scenario A / busy window -> reports the expected [NOTE] contradiction line (3 requests)"
    } else {
        Fail "Scenario A / busy window did not report the expected [NOTE] line. Output:`n$out"
    }

    # --- 4c. Scenario B against the BUSY window -> expect the "[ OK ]" HIT line
    $out = & $AnalyzeWindowsScript -Scenario B -From $busyFrom -To $busyTo -LogFile $FixtureLog -OutDir $TestTmpDir -NoReport *>&1 | Out-String
    if ($out -match '\[ OK \] 2 HIT request\(s\) observed') {
        Pass "Scenario B / busy window -> reports the expected OK line (2 HIT requests: the chunk + the manifest)"
    } else {
        Fail "Scenario B / busy window did not report the expected HIT-count OK line. Output:`n$out"
    }

    # --- 4d. Scenario B against the EMPTY window -> expect the "[NOTE]" zero-traffic path
    $out = & $AnalyzeWindowsScript -Scenario B -From $emptyFrom -To $emptyTo -LogFile $FixtureLog -OutDir $TestTmpDir -NoReport *>&1 | Out-String
    if ($out -match '\[NOTE\] 0 requests in window - unexpected for Scenario B') {
        Pass "Scenario B / empty window -> reports the expected [NOTE] zero-traffic line"
    } else {
        Fail "Scenario B / empty window did not report the expected [NOTE] line. Output:`n$out"
    }

    # --- 4e. no -Scenario -> just the shared report, no verdict block errors
    $out = & $AnalyzeWindowsScript -From $busyFrom -To $busyTo -LogFile $FixtureLog -OutDir $TestTmpDir -NoReport *>&1 | Out-String
    if ($out -match 'No -Scenario specified') {
        Pass "No -Scenario -> falls back to the plain 'no verdict' message without erroring"
    } else {
        Fail "No -Scenario run did not produce the expected fallback message. Output:`n$out"
    }

    # --- 4f. -NoReport actually suppresses the RESULTS-*.md write ------------
    $before = @(Get-ChildItem -Path $TestTmpDir -Filter "RESULTS-*.md" -ErrorAction SilentlyContinue).Count
    & $AnalyzeWindowsScript -Scenario A -From $busyFrom -To $busyTo -LogFile $FixtureLog -OutDir $TestTmpDir -NoReport *>&1 | Out-Null
    $after = @(Get-ChildItem -Path $TestTmpDir -Filter "RESULTS-*.md" -ErrorAction SilentlyContinue).Count
    if ($after -eq $before) {
        Pass "-NoReport did not write a RESULTS-*.md file"
    } else {
        Fail "-NoReport unexpectedly wrote a report file ($before -> $after)"
    }

    Remove-Item -Recurse -Force $TestTmpDir -ErrorAction SilentlyContinue
}
Write-Host ""

# =============================================================================
# 5. .gitattributes sanity (LF enforcement for future edits, not just this run)
# =============================================================================
Write-Host "-- 5: .gitattributes LF enforcement --" -ForegroundColor Cyan

$GitAttributesPath = Join-Path $KitDir "..\..\.gitattributes"
if (-not (Test-Path $GitAttributesPath)) {
    Fail ".gitattributes not found at repo root ($GitAttributesPath) - shell scripts have no enforced eol=lf rule"
} else {
    $content = Get-Content $GitAttributesPath -Raw
    if ($content -match [regex]::Escape("poc/linux-client-test/*.sh") -and $content -match "eol=lf") {
        Pass ".gitattributes contains an eol=lf rule for poc/linux-client-test/*.sh"
    } else {
        Fail ".gitattributes exists but does not appear to cover poc/linux-client-test/*.sh with eol=lf"
    }
}
Write-Host ""

# =============================================================================
# verdict
# =============================================================================
Write-Host "===================== SUMMARY =====================" -ForegroundColor Cyan
if ($script:failures.Count -eq 0) {
    Write-Host "PASS - $($script:passCount) assertion(s) passed, $($script:skipCount) skipped, 0 failed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL - $($script:passCount) passed, $($script:skipCount) skipped, $($script:failures.Count) failed:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
