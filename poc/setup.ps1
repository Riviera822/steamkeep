<#
.SYNOPSIS
    Phase 0 PoC setup: downloads and extracts the official nginx-for-Windows
    binary distribution and creates the directories the PoC config needs.

.DESCRIPTION
    Idempotent: if poc/nginx/nginx.exe already exists, the download/extract
    step is skipped. Safe to re-run.

    This script does NOT modify system settings, hosts file, or DNS. It only
    touches files under poc/.
#>

[CmdletBinding()]
param(
    [string]$NginxVersion = "1.30.4"
)

$ErrorActionPreference = "Stop"

$PocRoot   = $PSScriptRoot
$NginxDir  = Join-Path $PocRoot "nginx"
$CacheDir  = Join-Path $PocRoot "cache"
$TmpDir    = Join-Path $CacheDir "tmp"
$ClientBodyTmpDir = Join-Path $TmpDir "client_body"
$ProxyTmpDir = Join-Path $TmpDir "proxy"
$FastcgiTmpDir = Join-Path $TmpDir "fastcgi"
$UwsgiTmpDir = Join-Path $TmpDir "uwsgi"
$ScgiTmpDir = Join-Path $TmpDir "scgi"
$LogsDir   = Join-Path $PocRoot "logs"
$ZipPath   = Join-Path $PocRoot "nginx-$NginxVersion.zip"
$ZipUrl    = "https://nginx.org/download/nginx-$NginxVersion.zip"
$ExePath   = Join-Path $NginxDir "nginx.exe"

Write-Host "== SteamVault Phase 0 PoC setup ==" -ForegroundColor Cyan

# --- directories -----------------------------------------------------------
foreach ($dir in @($CacheDir, $TmpDir, $ClientBodyTmpDir, $ProxyTmpDir, $FastcgiTmpDir, $UwsgiTmpDir, $ScgiTmpDir, $LogsDir)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "Created $dir"
    }
}

# --- nginx download + extract (idempotent) ---------------------------------
if (Test-Path $ExePath) {
    Write-Host "nginx already present at $ExePath - skipping download." -ForegroundColor Yellow
}
else {
    Write-Host "Downloading nginx $NginxVersion from $ZipUrl ..."
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing

    Write-Host "Extracting ..."
    $ExtractTmp = Join-Path $PocRoot "_nginx_extract_tmp"
    if (Test-Path $ExtractTmp) { Remove-Item -Recurse -Force $ExtractTmp }
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractTmp -Force

    # The zip contains a single top-level "nginx-<version>" folder; flatten it
    # into poc/nginx/ so paths stay stable across versions.
    $inner = Get-ChildItem -Path $ExtractTmp -Directory | Select-Object -First 1
    if (-not $inner) { throw "Unexpected archive layout: no top-level directory found in $ZipPath" }
    if (Test-Path $NginxDir) { Remove-Item -Recurse -Force $NginxDir }
    Move-Item -Path $inner.FullName -Destination $NginxDir

    Remove-Item -Recurse -Force $ExtractTmp
    Write-Host "nginx extracted to $NginxDir" -ForegroundColor Green
}

if (-not (Test-Path $ExePath)) {
    throw "Setup failed: $ExePath not found after extraction."
}

Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  nginx binary : $ExePath"
Write-Host "  cache dir    : $CacheDir"
Write-Host "  logs dir     : $LogsDir"
Write-Host "Next: .\start.ps1"
