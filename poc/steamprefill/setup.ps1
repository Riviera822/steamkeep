<#
.SYNOPSIS
    Phase 0 PoC - WP 0.4: downloads the latest SteamPrefill Windows x64
    release into poc/steamprefill/bin/.

.DESCRIPTION
    Idempotent: if poc/steamprefill/bin/SteamPrefill.exe already exists, the
    download/extract step is skipped (use -Force to re-download anyway).

    Queries the GitHub API for the latest release of the official
    tpill90/steam-lancache-prefill repository (this is the correct repo name
    - the project also had a period of being named/known as "SteamPrefill"
    for the CLI binary itself; there is no repo literally named
    "steam-lan-prefill"), finds the "*-win-x64.zip" asset, downloads it, and
    extracts it. Verifies the extracted binary exists and does a basic size
    sanity check on the downloaded archive.

    This script does NOT modify system settings, hosts file, or DNS. It only
    touches files under poc/steamprefill/. It does NOT run SteamPrefill
    (no login, no select-apps, no prefill) - see PROTOCOL.md for the
    interactive part.

.PARAMETER Force
    Re-download and re-extract even if poc/steamprefill/bin/SteamPrefill.exe
    already exists.

.EXAMPLE
    .\setup.ps1
#>

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$Repo      = "tpill90/steam-lancache-prefill"
$ApiUrl    = "https://api.github.com/repos/$Repo/releases/latest"
$Root      = $PSScriptRoot
$BinDir    = Join-Path $Root "bin"
$ExePath   = Join-Path $BinDir "SteamPrefill.exe"
$MinZipBytes = 3MB   # sanity floor - a real win-x64 release is ~10-15 MB;
                      # anything drastically smaller signals a bad/partial
                      # download (e.g. an HTML error page saved as .zip)

Write-Host "== SteamVault Phase 0 PoC - WP 0.4 SteamPrefill setup ==" -ForegroundColor Cyan

if ((Test-Path $ExePath) -and -not $Force) {
    Write-Host "SteamPrefill already present at $ExePath - skipping download." -ForegroundColor Yellow
    Write-Host "(use -Force to re-download the latest release anyway)"
    exit 0
}

if (-not (Test-Path $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    Write-Host "Created $BinDir"
}

# --- query GitHub API for the latest release --------------------------------
Write-Host "Querying $ApiUrl for the latest release ..."
try {
    $headers = @{ "User-Agent" = "SteamVault-PoC-WP0.4" ; "Accept" = "application/vnd.github+json" }
    $release = Invoke-RestMethod -Uri $ApiUrl -Headers $headers -UseBasicParsing
} catch {
    throw "Failed to query GitHub API for the latest release of $Repo`: $_"
}

$version = $release.tag_name
if (-not $version) { throw "GitHub API response had no tag_name - unexpected release payload." }

$asset = $release.assets | Where-Object { $_.name -match '(?i)win-x64\.zip$' } | Select-Object -First 1
if (-not $asset) {
    $names = ($release.assets | ForEach-Object { $_.name }) -join ", "
    throw "No *-win-x64.zip asset found in release $version. Assets present: $names"
}

$assetName = $asset.name
$assetUrl  = $asset.browser_download_url
$assetSize = [int64]$asset.size

Write-Host "Latest release: $version" -ForegroundColor Green
Write-Host "Asset: $assetName ($([Math]::Round($assetSize / 1MB, 1)) MiB)"
Write-Host "URL: $assetUrl"

# --- download -----------------------------------------------------------------
$ZipPath = Join-Path $Root $assetName
Write-Host "Downloading ..."
Invoke-WebRequest -Uri $assetUrl -OutFile $ZipPath -UseBasicParsing

$downloadedSize = (Get-Item $ZipPath).Length
Write-Host "Downloaded $([Math]::Round($downloadedSize / 1MB, 1)) MiB to $ZipPath"

if ($downloadedSize -lt $MinZipBytes) {
    throw "Downloaded file is only $downloadedSize bytes (expected at least $MinZipBytes) - looks truncated or wrong. Not extracting. Check $ZipPath manually."
}
if ($assetSize -gt 0 -and [Math]::Abs($downloadedSize - $assetSize) -gt 1024) {
    Write-Host "WARNING: downloaded size ($downloadedSize bytes) differs from the release asset's advertised size ($assetSize bytes) by more than 1 KiB." -ForegroundColor Yellow
}

# --- extract --------------------------------------------------------------------
Write-Host "Extracting ..."
$ExtractTmp = Join-Path $Root "_steamprefill_extract_tmp"
if (Test-Path $ExtractTmp) { Remove-Item -Recurse -Force $ExtractTmp }
Expand-Archive -Path $ZipPath -DestinationPath $ExtractTmp -Force

# The release zip layout has varied historically (flat files vs. a nested
# folder) - handle both: if the archive root contains SteamPrefill.exe
# directly, copy the whole extracted tree into bin/; if it contains exactly
# one subdirectory, flatten that instead.
$topLevel = Get-ChildItem -Path $ExtractTmp
$sourceDir = $ExtractTmp
if (-not (Get-ChildItem -Path $ExtractTmp -Filter "SteamPrefill.exe" -File -ErrorAction SilentlyContinue)) {
    $inner = $topLevel | Where-Object { $_.PSIsContainer } | Select-Object -First 1
    if ($inner) { $sourceDir = $inner.FullName }
}

if (Test-Path $BinDir) { Remove-Item -Recurse -Force $BinDir }
Move-Item -Path $sourceDir -Destination $BinDir

if ($sourceDir -ne $ExtractTmp -and (Test-Path $ExtractTmp)) {
    Remove-Item -Recurse -Force $ExtractTmp -ErrorAction SilentlyContinue
}
Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue

if (-not (Test-Path $ExePath)) {
    throw "Setup failed: $ExePath not found after extraction. Check $BinDir contents manually."
}

Write-Host "Extracted to $BinDir" -ForegroundColor Green

# --- sanity-run the binary (version check only - no login, no network calls
#     to Steam) ------------------------------------------------------------------
Write-Host ""
Write-Host "Verifying the binary runs (--version) ..."
try {
    $verOutput = & $ExePath --version 2>&1
    Write-Host ($verOutput -join "`n")
} catch {
    Write-Host "WARNING: could not run '$ExePath --version': $_" -ForegroundColor Yellow
    Write-Host "The binary was downloaded and extracted, but the version check itself failed - inspect manually." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  release      : $version"
Write-Host "  binary       : $ExePath"
Write-Host "Next: see PROTOCOL.md for the interactive login/select-apps/prefill steps."
