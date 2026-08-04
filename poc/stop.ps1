<#
.SYNOPSIS
    Stops the SteamVault Phase 0 PoC nginx instance gracefully.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$PocRoot  = $PSScriptRoot
$NginxExe = Join-Path $PocRoot "nginx\nginx.exe"

$running = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
if (-not $running) {
    Write-Host "nginx is not running." -ForegroundColor Yellow
    exit 0
}

if (Test-Path $NginxExe) {
    Write-Host "Stopping nginx (graceful) ..."
    & $NginxExe -p "$PocRoot" -c "conf/nginx.conf" -s stop
    Start-Sleep -Milliseconds 500
}

$stillRunning = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
if ($stillRunning) {
    Write-Host "nginx still running after graceful stop, forcing termination ..." -ForegroundColor Yellow
    $stillRunning | Stop-Process -Force
}

Write-Host "nginx stopped." -ForegroundColor Green
