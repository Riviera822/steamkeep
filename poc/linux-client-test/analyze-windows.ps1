<#
.SYNOPSIS
    WP 0.6 - thin Windows-side wrapper around poc/steam-client-test/analyze.ps1
    (WP 0.3) for the Linux-Steam-client (WSL2) test kit. Answers: did ANY
    traffic arrive at the cache in this window (Scenario A expects zero),
    and the usual conformance/hit-ratio stats (Scenario B).

.DESCRIPTION
    Deliberately thin - all the actual log parsing/windowing/aggregation
    logic lives in poc/steam-client-test/analyze.ps1 and is reused here
    as-is (same access log, same -From/-To semantics, same RESULTS-*.md
    report format), nothing is re-implemented. This script only adds a
    short scenario-specific verdict on top of that shared report:

      -Scenario A (hosts mode): PASS if zero requests fall in the window -
       the expected, correct "null result" (the known upstream quirk that
       the Linux client does not perform the lancache.steamcontent.com
       lookup at all). Any traffic in the window is reported as a
       surprising finding worth investigating, not silently swallowed.

      -Scenario B (DNS-rewrite mode): narrates the shared report's hit/miss
       numbers against the "traffic should reach the cache, warm objects
       should HIT" expectation. No separate pass/fail gate beyond what
       analyze.ps1 itself already computes.

.PARAMETER From
    Start of the analysis window, same format analyze.ps1 accepts (parsed
    with [datetime]::Parse), e.g. "2026-08-10 09:15:00". Use the timestamp
    marker scenario-a.sh / scenario-b.sh print right before you start Steam.

.PARAMETER To
    End of the analysis window.

.PARAMETER Scenario
    "A" or "B" - selects which verdict text to print below the shared
    report. Optional; omit to just run analyze.ps1's report with no extra
    scenario verdict.

.PARAMETER LogFile
    Passed straight through to analyze.ps1. Defaults to poc/logs/access.log
    - the SAME log the Windows-client test (WP 0.3) writes to. There is no
    separate log for the Linux-client test; both clients share one nginx
    instance and one cache (see PROTOCOL.md section 5.3).

.PARAMETER OutDir
    Passed straight through to analyze.ps1 (where its RESULTS-*.md is
    written). Defaults to this script's own directory, so Linux-client-test
    reports don't mix into steam-client-test/'s RESULTS-*.md files.

.PARAMETER NoReport
    Passed straight through to analyze.ps1 (skip writing RESULTS-*.md).

.EXAMPLE
    .\analyze-windows.ps1 -Scenario A -From "2026-08-10 09:15:00" -To "2026-08-10 09:20:00"

.EXAMPLE
    .\analyze-windows.ps1 -Scenario B -From "2026-08-10 09:30:00" -To "2026-08-10 09:40:00"
#>

[CmdletBinding()]
param(
    [string]$From = "",
    [string]$To = "",
    [ValidateSet("A", "B", "")]
    [string]$Scenario = "",
    [string]$LogFile = (Join-Path $PSScriptRoot "..\logs\access.log"),
    [string]$OutDir = $PSScriptRoot,
    [switch]$NoReport
)

$ErrorActionPreference = "Stop"

$AnalyzeScript = Join-Path $PSScriptRoot "..\steam-client-test\analyze.ps1"
if (-not (Test-Path $AnalyzeScript)) {
    throw "Could not find poc/steam-client-test/analyze.ps1 at '$AnalyzeScript' - this wrapper has nothing to reuse without it (WP 0.3 must exist alongside WP 0.6)."
}

# --- reuse the WP 0.3 analysis engine as-is, no logic duplicated here -------
$analyzeArgs = @{
    LogFile  = $LogFile
    From     = $From
    To       = $To
    OutDir   = $OutDir
    PassThru = $true
}
if ($NoReport) { $analyzeArgs['NoReport'] = $true }

$result = & $AnalyzeScript @analyzeArgs | Select-Object -Last 1

if (-not $result) {
    throw "analyze.ps1 returned no result object - see its own output above for the failure."
}

# --- scenario-specific verdict on top of the shared report ------------------
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host " WP 0.6 - Linux-Steam-client (WSL2) scenario verdict" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan

$totalInWindow = $result.Meta.WindowLines

switch ($Scenario) {
    "A" {
        Write-Host ""
        Write-Host "Scenario A (hosts mode) expectation: ZERO traffic at the cache -" -ForegroundColor Yellow
        Write-Host "the Linux Steam client is not known to perform the" -ForegroundColor Yellow
        Write-Host "lancache.steamcontent.com lookup (docs/PROJECT_PLAN.md section 7)." -ForegroundColor Yellow
        Write-Host ""
        if ($totalInWindow -eq 0) {
            Write-Host "[ OK ] 0 requests in window - the expected null result. This IS the evidence." -ForegroundColor Green
        } else {
            Write-Host "[NOTE] $totalInWindow request(s) found in this window - NOT the expected null result." -ForegroundColor Red
            Write-Host "       This would contradict the known upstream quirk and is worth investigating" -ForegroundColor Red
            Write-Host "       further (see the URI/per-depot sections above) rather than assumed to be a" -ForegroundColor Red
            Write-Host "       script bug - re-check the window bounds and dnsmasq state first." -ForegroundColor Red
        }
    }
    "B" {
        Write-Host ""
        Write-Host "Scenario B (DNS-rewrite mode) expectation: traffic DOES reach the" -ForegroundColor Yellow
        Write-Host "cache, and warm objects come back as HIT. See sections 1/3 of the" -ForegroundColor Yellow
        Write-Host "report above for conformance and hit-ratio numbers." -ForegroundColor Yellow
        Write-Host ""
        if ($totalInWindow -eq 0) {
            Write-Host "[NOTE] 0 requests in window - unexpected for Scenario B. Re-check the DNS" -ForegroundColor Red
            Write-Host "       state (re-run scenario-b.sh, it re-verifies every time) and the window" -ForegroundColor Red
            Write-Host "       bounds before concluding the DNS-rewrite approach doesn't work." -ForegroundColor Red
        } elseif ($result.HitMiss.HitCount -gt 0) {
            Write-Host "[ OK ] $($result.HitMiss.HitCount) HIT request(s) observed - warm objects served from cache." -ForegroundColor Green
        } else {
            Write-Host "[NOTE] Traffic reached the cache ($totalInWindow requests) but 0 were HIT -" -ForegroundColor Yellow
            Write-Host "       expected if this was the game's FIRST download here (all-MISS is correct" -ForegroundColor Yellow
            Write-Host "       then). Re-run after an uninstall/reinstall cycle to see HITs, same idea" -ForegroundColor Yellow
            Write-Host "       as WP 0.3's second run." -ForegroundColor Yellow
        }
    }
    default {
        Write-Host ""
        Write-Host "No -Scenario specified - see the analyze.ps1 report above for the raw numbers." -ForegroundColor Gray
    }
}

Write-Host ""
