<#
.SYNOPSIS
    Starts the SteamVault Phase 0 PoC nginx instance in the background.

.PARAMETER Config
    Name of the config file under poc/conf/ to start nginx with. Defaults to
    "nginx.conf" (the WP 0.1 store-mode baseline) — bare `.\start.ps1` behaves
    exactly as before this parameter was added. Pass "nginx-passthrough.conf"
    (WP 0.5) to run the transparent-passthrough variant instead.
#>

[CmdletBinding()]
param(
    [string]$Config = "nginx.conf"
)

$ErrorActionPreference = "Stop"

$PocRoot = $PSScriptRoot
$NginxExe = Join-Path $PocRoot "nginx\nginx.exe"
$ConfigPath = Join-Path $PocRoot "conf\$Config"

if (-not (Test-Path $NginxExe)) {
    throw "nginx.exe not found at $NginxExe. Run .\setup.ps1 first."
}
if (-not (Test-Path $ConfigPath)) {
    throw "Config file not found at $ConfigPath."
}

# Already running?
$running = Get-Process -Name "nginx" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "nginx already running (PID $($running.Id -join ', '))." -ForegroundColor Yellow
    exit 0
}

Write-Host "Starting nginx (prefix=$PocRoot, config=conf/$Config) ..."

# nginx.exe daemonizes itself on Windows the same way it does on the
# console it's launched from; use Start-Process so this script returns
# immediately once nginx has forked/bound the port.
Start-Process -FilePath $NginxExe `
    -ArgumentList @("-p", "`"$PocRoot`"", "-c", "conf/$Config") `
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
