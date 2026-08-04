<#
.SYNOPSIS
    Starts the SteamVault Phase 0 PoC nginx instance in the background.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$PocRoot = $PSScriptRoot
$NginxExe = Join-Path $PocRoot "nginx\nginx.exe"

if (-not (Test-Path $NginxExe)) {
    throw "nginx.exe not found at $NginxExe. Run .\setup.ps1 first."
}

# Already running?
$running = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "nginx already running (PID $($running.Id -join ', '))." -ForegroundColor Yellow
    exit 0
}

Write-Host "Starting nginx (prefix=$PocRoot, config=conf/nginx.conf) ..."

# nginx.exe daemonizes itself on Windows the same way it does on the
# console it's launched from; use Start-Process so this script returns
# immediately once nginx has forked/bound the port.
Start-Process -FilePath $NginxExe `
    -ArgumentList @("-p", "`"$PocRoot`"", "-c", "conf/nginx.conf") `
    -WorkingDirectory $PocRoot `
    -WindowStyle Hidden

Start-Sleep -Milliseconds 500

$proc = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "nginx started (PID $($proc.Id -join ', '))." -ForegroundColor Green
    Write-Host "Test: curl.exe -i http://127.0.0.1/health"
}
else {
    Write-Host "nginx did not appear to start - check poc/logs/error.log" -ForegroundColor Red
    exit 1
}
