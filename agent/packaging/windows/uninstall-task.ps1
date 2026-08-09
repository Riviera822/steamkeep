<#
.SYNOPSIS
    Removes exactly what install-task.ps1 created  -  WP 2.6.

.DESCRIPTION
    Unregisters the Scheduled Task and deletes only the specific files
    install-task.ps1 is known to have written inside -ConfigDir (env.txt,
    run-vault-agent.ps1, vault-agent.log)  -  never the whole directory
    wholesale, in case an operator put something else in there. The
    directory itself is removed only if it ends up empty. vault-agent.exe
    (-AgentPath at install time) is never touched: it was never copied or
    owned by install-task.ps1 in the first place.

    Refuses gracefully (exit 0, explanatory message) when the task is
    already absent, so re-running this script (or running it after a
    partial/failed install) is always safe.

.PARAMETER TaskName
    Must match the -TaskName given to install-task.ps1. Default:
    VaultAgentReport.

.PARAMETER ConfigDir
    Must match the -ConfigDir given to install-task.ps1. Default:
    $env:LOCALAPPDATA\VaultAgent.

.EXAMPLE
    .\uninstall-task.ps1

.EXAMPLE
    .\uninstall-task.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $false)]
    [string]$TaskName = "VaultAgentReport",

    [Parameter(Mandatory = $false)]
    [string]$ConfigDir = (Join-Path $env:LOCALAPPDATA "VaultAgent")
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if (-not $task) {
    Write-Host "Scheduled Task '$TaskName' is not installed  -  nothing to do."
} else {
    if ($PSCmdlet.ShouldProcess($TaskName, "Unregister Scheduled Task")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed Scheduled Task '$TaskName'."
    }
}

# Only the specific files install-task.ps1 is documented to write  - 
# never a blanket directory wipe.
$knownFiles = @(
    (Join-Path $ConfigDir "env.txt"),
    (Join-Path $ConfigDir "run-vault-agent.ps1"),
    (Join-Path $ConfigDir "vault-agent.log")
)

$removedAny = $false
foreach ($filePath in $knownFiles) {
    if (Test-Path -LiteralPath $filePath) {
        if ($PSCmdlet.ShouldProcess($filePath, "Remove file")) {
            Remove-Item -LiteralPath $filePath -Force
            Write-Host "Removed $filePath"
            $removedAny = $true
        }
    }
}

if (Test-Path -LiteralPath $ConfigDir) {
    $remaining = @(Get-ChildItem -LiteralPath $ConfigDir -Force -ErrorAction SilentlyContinue)
    if ($remaining.Count -eq 0) {
        if ($PSCmdlet.ShouldProcess($ConfigDir, "Remove empty config directory")) {
            Remove-Item -LiteralPath $ConfigDir -Force
            Write-Host "Removed empty config directory $ConfigDir"
        }
    } elseif ($removedAny) {
        Write-Host "Left $ConfigDir in place  -  it still contains other files:"
        foreach ($item in $remaining) { Write-Host "  $($item.FullName)" }
    }
}

if (-not $task -and -not $removedAny) {
    Write-Host "Nothing found for '$TaskName' / '$ConfigDir'  -  already clean."
}
