<#
.SYNOPSIS
    PSScriptAnalyzer PSUseCompatibleSyntax(5.1) gate for the agent packaging
    scripts (WP 5.1, CI). Runs under `pwsh` -- see verify-ps-parse.ps1 for
    the parser + pure-ASCII half, which runs under real Windows PowerShell
    5.1 instead.

.DESCRIPTION
    Review round 2 (S5): round 1 ran PSScriptAnalyzer under `shell:
    powershell` (Windows PowerShell 5.1) on the assumption that
    actions/runner-images' preinstalled PSScriptAnalyzer (confirmed present
    on windows-latest, version 1.25.0) is visible there. The reviewer
    correctly flagged that this is not actually guaranteed: PowerShell 5.1
    and PowerShell 7 (pwsh) resolve $env:PSModulePath from separate default
    locations, and a module installed into (or shipped under) one edition's
    path is not automatically visible to the other. The real uncertainty is
    which of the runner image's module paths its preinstalled
    PSScriptAnalyzer actually lives under, and whether that path is on
    Windows PowerShell 5.1's $env:PSModulePath by default -- not something
    this WP's dev machine can settle either way (its own PSScriptAnalyzer
    was separately installed for testing, so checking it there proves
    nothing about the clean-runner case).

    The fix is a split, not a blind fallback: PSUseCompatibleSyntax's job
    is to compare a script's AST against stored per-version compatibility
    PROFILE DATA for the TargetVersions setting below -- it does not need to
    run under the PowerShell version it's checking FOR (unlike
    verify-ps-parse.ps1's parser check, which genuinely does). So this half
    of the gate runs under `pwsh`, which every GitHub-hosted Windows runner
    ships natively and where PSScriptAnalyzer is reliably discoverable.

    A guarded, one-time Install-Module fallback is kept anyway as pure
    defense-in-depth in case that assumption is ever wrong on some future
    runner image: it only triggers if Import-Module fails, costs a few
    seconds against PSGallery in the (expected-never) worst case, and fails
    loudly with a clear message rather than silently skipping the check if
    even that doesn't work. This isn't the network dependency the WP 5.1
    brief warned about avoiding -- it never runs on a healthy runner.

    Deliberately does NOT execute any of the scripts it checks -- same
    reasoning as verify-ps-parse.ps1.
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$targetDir = Join-Path $repoRoot "agent\packaging\windows"

if (-not (Test-Path -LiteralPath $targetDir)) {
    throw "Target directory does not exist: $targetDir"
}

$files = @(Get-ChildItem -Path $targetDir -Filter *.ps1 -Recurse)
if ($files.Count -eq 0) {
    throw "No .ps1 files found under $targetDir -- nothing was actually checked."
}

Write-Output "Running under PSVersion $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))"
Write-Output "Checking $($files.Count) script(s) under $targetDir for PSUseCompatibleSyntax(5.1)"

if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) {
    Write-Output "PSScriptAnalyzer not visible under this host -- attempting a one-time CurrentUser install as a fallback (see .DESCRIPTION)."
    Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -Confirm:$false -ErrorAction Stop
}
Import-Module PSScriptAnalyzer -ErrorAction Stop

$settings = @{
    IncludeRules = @('PSUseCompatibleSyntax')
    Rules         = @{
        PSUseCompatibleSyntax = @{
            Enable         = $true
            TargetVersions = @('5.1')
        }
    }
}

$analyzerFailed = $false
foreach ($f in $files) {
    $rel = $f.FullName.Substring($repoRoot.Length + 1)
    $results = @(Invoke-ScriptAnalyzer -Path $f.FullName -Settings $settings)
    if ($results.Count -gt 0) {
        $analyzerFailed = $true
        Write-Output "PSUseCompatibleSyntax (target 5.1) violation: $rel"
        foreach ($r in $results) {
            Write-Output "  line $($r.Line): $($r.Message)"
        }
    } else {
        Write-Output "  5.1-compatible: $rel"
    }
}

if ($analyzerFailed) {
    throw "One or more scripts use syntax incompatible with PowerShell 5.1 -- see output above."
}

Write-Output "All packaging scripts are PSUseCompatibleSyntax-clean for PowerShell 5.1."
