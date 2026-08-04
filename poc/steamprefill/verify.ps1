<#
.SYNOPSIS
    Phase 0 PoC - WP 0.4: evaluates whether SteamPrefill filled the PoC cache
    (poc/conf/nginx.conf) correctly and path-faithfully, per
    docs/PROJECT_PLAN.md section 7.

.DESCRIPTION
    Standalone script (does not dot-source poc/steam-client-test/analyze.ps1
    - the log format is shared, but this script is self-contained on
    purpose). Combines two kinds of evidence:

      1. Log evidence from poc/logs/access.log, for the SteamPrefill run's
         time window specifically:
         - URI-scheme conformance: chunk / manifest / patch / other, per
           the real Steam CDN URL schemes actually observed in this PoC's
           log across all work packages so far (patch downloads use
           /depot/<id>/patch/<fromManifestId>/<toManifestId>, in addition
           to the chunk/manifest schemes WP 0.3's analyze.ps1 already
           covers).
         - Range usage BY STEAMPREFILL specifically - new evidence vs.
           WP 0.3 (the real Windows Steam client used zero Range requests;
           does SteamPrefill?).
         - Hit/miss split and bytes fetched.
         - Per-depot request/byte counts.

      2. Filesystem evidence from poc/cache/depot/: for every depot ID this
         run's log entries touched, counts + total bytes of files under
         its chunk/, manifest/, and patch/ subfolders, plus a strict
         cross-check that every file under chunk/ is named as a bare
         40-hex-character SHA1 (docs/PROJECT_PLAN.md section 4's
         path-faithful layout) - any file that does NOT match that
         pattern verbatim is listed, as is anything stored directly under
         a depot's own directory outside the chunk/manifest/patch
         subfolders (which would itself be a layout violation).

    The log window is either given explicitly (-From/-To, same convention
    as analyze.ps1) or auto-detected as the newest contiguous burst of
    traffic in the log (entries where the gap to the previous entry is at
    most -BurstGapSeconds apart; a gap larger than that starts a new
    burst). This lets the script find "the SteamPrefill run" in a log file
    that may also contain earlier WP 0.1/0.2/0.3 traffic, without the user
    having to note exact timestamps.

    If the detected/given window contains zero /depot/-scheme requests at
    all, the script prints and writes an explicit "no SteamPrefill traffic
    detected" result instead of a misleading all-zeros report. As of this
    writing, poc/conf/nginx.conf and poc/conf/nginx-passthrough.conf both
    implement the /lancache-heartbeat + X-LanCache-Processed-By contract
    SteamPrefill's auto-detection requires (see PROTOCOL.md section 0), so
    `prefill` is expected to proceed to real depot requests rather than
    abort - a "no traffic detected" result more likely means `prefill`
    wasn't actually run in this window, or the window doesn't cover it,
    than the historical heartbeat gap.

.PARAMETER LogFile
    Path to the vault access log. Defaults to poc/logs/access.log relative
    to this script.

.PARAMETER CacheDepotDir
    Path to the depot cache directory. Defaults to poc/cache/depot
    relative to this script.

.PARAMETER From
    Optional lower bound (inclusive), parsed with [datetime]::Parse. If
    given together with -To, disables burst auto-detection - the exact
    window is used as-is (same as analyze.ps1).

.PARAMETER To
    Optional upper bound (inclusive), same format as -From.

.PARAMETER BurstGapSeconds
    When -From/-To are not both given, entries more than this many seconds
    apart (by the log's own wall-clock timestamp) are considered separate
    bursts; the newest (chronologically last) burst is analyzed. Default
    30 seconds.

.PARAMETER OutDir
    Directory the RESULTS-STEAMPREFILL-<timestamp>.md file is written to.
    Defaults to this script's own directory.

.PARAMETER NoReport
    Skip writing the RESULTS-STEAMPREFILL-<timestamp>.md file (console
    output only). Used by test-verify.ps1.

.PARAMETER PassThru
    Also emit the computed result as a PowerShell object on the pipeline,
    so it can be asserted on programmatically. Used by test-verify.ps1.

.EXAMPLE
    .\verify.ps1
    Auto-detects the newest burst of traffic and analyzes it.

.EXAMPLE
    .\verify.ps1 -From "2026-08-04 19:10:00" -To "2026-08-04 19:25:00"
    Analyzes an explicit window (e.g. bracketing your own noted
    prefill/prefill --force run times).
#>

[CmdletBinding()]
param(
    [string]$LogFile        = (Join-Path $PSScriptRoot "..\logs\access.log"),
    [string]$CacheDepotDir  = (Join-Path $PSScriptRoot "..\cache\depot"),
    [string]$From           = "",
    [string]$To             = "",
    [double]$BurstGapSeconds = 30,
    [string]$OutDir         = $PSScriptRoot,
    [switch]$NoReport,
    [switch]$PassThru
)

$ErrorActionPreference = "Stop"

# --- log line parsing (same "vault" log_format as poc/conf/nginx.conf; see
#     poc/README.md "Access log format") -----------------------------------
$script:LinePattern = '^(?<time>\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}) ' +
    'uri="(?<uri>[^"]*)" status=(?<status>\d+) range="(?<range>[^"]*)" ' +
    'upstream_status=(?<ustatus>\S+) bytes_sent=(?<bytes>\d+) ' +
    'request_time=(?<rtime>[0-9.]+) cache=(?<cache>\S+)$'

$script:MonthNumbers = @{
    Jan = 1; Feb = 2; Mar = 3; Apr = 4; May = 5; Jun = 6
    Jul = 7; Aug = 8; Sep = 9; Oct = 10; Nov = 11; Dec = 12
}

function ConvertTo-VaultDateTime {
    param([string]$NginxTime)
    if ($NginxTime -match '^(?<d>\d{2})/(?<mon>[A-Za-z]{3})/(?<y>\d{4}):(?<h>\d{2}):(?<mi>\d{2}):(?<s>\d{2}) [+-]\d{4}$') {
        $monNum = $script:MonthNumbers[$Matches['mon']]
        if (-not $monNum) { return $null }
        try {
            return Get-Date -Year ([int]$Matches['y']) -Month $monNum -Day ([int]$Matches['d']) `
                -Hour ([int]$Matches['h']) -Minute ([int]$Matches['mi']) -Second ([int]$Matches['s']) -Millisecond 0
        } catch { return $null }
    }
    return $null
}

function ConvertFrom-VaultLogLine {
    param([string]$Line)
    if ($Line -notmatch $script:LinePattern) { return $null }
    $time = ConvertTo-VaultDateTime $Matches['time']
    if (-not $time) { return $null }
    [pscustomobject]@{
        Time           = $time
        Uri            = $Matches['uri']
        Status         = [int]$Matches['status']
        Range          = $Matches['range']
        UpstreamStatus = $Matches['ustatus']
        BytesSent      = [int64]$Matches['bytes']
        RequestTime    = [double]$Matches['rtime']
        Cache          = $Matches['cache']
    }
}

function Get-UriInfo {
    <#
        Classifies a request URI against the Steam CDN schemes actually
        observed across this PoC's work packages: chunk, manifest, patch,
        or other (non-conforming). Unlike WP 0.3's analyze.ps1 (which only
        knows chunk/manifest), this also recognizes the patch scheme
        (/depot/<id>/patch/<fromManifestId>/<toManifestId>), confirmed
        present in poc/logs/access.log from earlier PoC work.
    #>
    param([string]$Uri)

    if ($Uri -match '^/depot/(?<depot>\d+)/chunk/(?<hash>[0-9a-fA-F]{40})$') {
        return [pscustomobject]@{ Class = 'chunk'; DepotId = $Matches['depot'] }
    }
    if ($Uri -match '^/depot/(?<depot>\d+)/patch/\d+/\d+$') {
        return [pscustomobject]@{ Class = 'patch'; DepotId = $Matches['depot'] }
    }
    if ($Uri -match '^/depot/(?<depot>\d+)/manifest/') {
        return [pscustomobject]@{ Class = 'manifest'; DepotId = $Matches['depot'] }
    }
    return [pscustomobject]@{ Class = 'other'; DepotId = $null }
}

function Get-RangeClass {
    param([string]$Range)
    if ([string]::IsNullOrWhiteSpace($Range) -or $Range -eq '-') { return 'none' }
    if ($Range -notmatch '^bytes=') { return 'other' }
    $spec = $Range -replace '^bytes=', ''
    if ($spec -match ',') { return 'multi' }
    if ($spec -match '^-\d+$') { return 'suffix' }
    if ($spec -match '^\d+-\d*$') { return 'explicit' }
    return 'other'
}

$script:Inv = [System.Globalization.CultureInfo]::InvariantCulture

function Format-Num {
    param([double]$Value, [string]$Format = "N1")
    return $Value.ToString($Format, $script:Inv)
}
function Format-Pct {
    param([double]$Num, [double]$Den)
    if ($Den -le 0) { return "n/a" }
    return (Format-Num (100.0 * $Num / $Den) "N1") + "%"
}
function Format-Bytes {
    param([double]$Bytes)
    if ($Bytes -ge 1GB) { return (Format-Num ($Bytes / 1GB) "N2") + " GiB" }
    if ($Bytes -ge 1MB) { return (Format-Num ($Bytes / 1MB) "N2") + " MiB" }
    if ($Bytes -ge 1KB) { return (Format-Num ($Bytes / 1KB) "N2") + " KiB" }
    return "$Bytes B"
}

# --- 1. load + parse ----------------------------------------------------------

if (-not (Test-Path $LogFile)) {
    throw "Log file not found: $LogFile (run the PoC and generate some SteamPrefill traffic first - see PROTOCOL.md)"
}

$rawLines = Get-Content $LogFile
$parsed = @()
$skipped = 0
foreach ($line in $rawLines) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $entry = ConvertFrom-VaultLogLine $line
    if ($entry) { $parsed += $entry } else { $skipped++ }
}
$parsed = @($parsed | Sort-Object Time)

# --- 2. window: explicit -From/-To, or auto-detected newest burst -------------

$windowMode = ""
$fromDt = $null
$toDt = $null
$burstInfo = $null

if ($From -ne "" -or $To -ne "") {
    if ($From -ne "") { try { $fromDt = [datetime]::Parse($From) } catch { throw "Could not parse -From '$From': $_" } }
    if ($To   -ne "") { try { $toDt   = [datetime]::Parse($To)   } catch { throw "Could not parse -To '$To': $_" } }
    $windowMode = "explicit"
}
elseif ($parsed.Count -gt 0) {
    # Group into bursts: a gap > BurstGapSeconds between consecutive
    # (already time-sorted) entries starts a new burst. The last burst
    # built is the chronologically newest one.
    $bursts = New-Object System.Collections.Generic.List[object]
    $current = New-Object System.Collections.Generic.List[object]
    $current.Add($parsed[0])
    for ($i = 1; $i -lt $parsed.Count; $i++) {
        $gap = ($parsed[$i].Time - $parsed[$i - 1].Time).TotalSeconds
        if ($gap -gt $BurstGapSeconds) {
            $bursts.Add($current.ToArray())
            $current = New-Object System.Collections.Generic.List[object]
        }
        $current.Add($parsed[$i])
    }
    $bursts.Add($current.ToArray())

    $lastBurst = @($bursts[$bursts.Count - 1])
    $fromDt = ($lastBurst | Measure-Object -Property Time -Minimum).Minimum
    $toDt   = ($lastBurst | Measure-Object -Property Time -Maximum).Maximum
    $burstInfo = [pscustomobject]@{
        TotalBursts   = $bursts.Count
        ChosenCount   = $lastBurst.Count
        GapSeconds    = $BurstGapSeconds
    }
    $windowMode = "auto-burst"
}
else {
    $windowMode = "empty-log"
}

$windowed = $parsed
if ($fromDt) { $windowed = $windowed | Where-Object { $_.Time -ge $fromDt } }
if ($toDt)   { $windowed = $windowed | Where-Object { $_.Time -le $toDt } }
$windowed = @($windowed)

$totalAll = $parsed.Count
$total = $windowed.Count

# --- 3. classify + check for "no SteamPrefill traffic" early-out --------------

$classified = $windowed | ForEach-Object {
    $info = Get-UriInfo $_.Uri
    [pscustomobject]@{ Entry = $_; Class = $info.Class; DepotId = $info.DepotId }
}

$chunkCount    = @($classified | Where-Object { $_.Class -eq 'chunk' }).Count
$manifestCount = @($classified | Where-Object { $_.Class -eq 'manifest' }).Count
$patchCount    = @($classified | Where-Object { $_.Class -eq 'patch' }).Count
$otherEntries  = @($classified | Where-Object { $_.Class -eq 'other' })
$otherCount    = $otherEntries.Count

$depotSchemeCount = $chunkCount + $manifestCount + $patchCount
$noPrefillTrafficDetected = ($depotSchemeCount -eq 0)

$otherUriList = $otherEntries | Group-Object { $_.Entry.Uri } |
    Sort-Object -Property @{ Expression = 'Count'; Descending = $true }, @{ Expression = 'Name'; Descending = $false } |
    ForEach-Object { [pscustomobject]@{ Uri = $_.Name; Count = $_.Count } }

# --- 4. range usage -------------------------------------------------------------

$rangeClasses = $windowed | ForEach-Object { Get-RangeClass $_.Range }
$rangeNone     = @($rangeClasses | Where-Object { $_ -eq 'none' }).Count
$rangeSuffix   = @($rangeClasses | Where-Object { $_ -eq 'suffix' }).Count
$rangeExplicit = @($rangeClasses | Where-Object { $_ -eq 'explicit' }).Count
$rangeMulti    = @($rangeClasses | Where-Object { $_ -eq 'multi' }).Count
$rangeOther    = @($rangeClasses | Where-Object { $_ -eq 'other' }).Count
$rangeUsedTotal = $total - $rangeNone

# --- 5. hit/miss split + bytes ---------------------------------------------------

$hitEntries  = @($windowed | Where-Object { $_.Cache -eq 'HIT' })
$missEntries = @($windowed | Where-Object { $_.Cache -eq 'MISS' })
$otherCacheEntries = @($windowed | Where-Object { $_.Cache -ne 'HIT' -and $_.Cache -ne 'MISS' })

$hitCount  = $hitEntries.Count
$missCount = $missEntries.Count
$decidedCount = $hitCount + $missCount

$hitBytes  = ($hitEntries  | Measure-Object -Property BytesSent -Sum).Sum
$missBytes = ($missEntries | Measure-Object -Property BytesSent -Sum).Sum
if (-not $hitBytes)  { $hitBytes  = 0 }
if (-not $missBytes) { $missBytes = 0 }
$totalBytes = ($windowed | Measure-Object -Property BytesSent -Sum).Sum
if (-not $totalBytes) { $totalBytes = 0 }

$hitRatio = if ($decidedCount -gt 0) { 100.0 * $hitCount / $decidedCount } else { $null }

# --- 6. per-depot request/byte counts (from the log) ----------------------------

$perDepot = $classified | Where-Object { $_.DepotId } | Group-Object DepotId | ForEach-Object {
    $bytes = ($_.Group | ForEach-Object { $_.Entry.BytesSent } | Measure-Object -Sum).Sum
    $chunkN    = @($_.Group | Where-Object { $_.Class -eq 'chunk' }).Count
    $manifestN = @($_.Group | Where-Object { $_.Class -eq 'manifest' }).Count
    $patchN    = @($_.Group | Where-Object { $_.Class -eq 'patch' }).Count
    [pscustomobject]@{
        DepotId      = $_.Name
        Requests     = $_.Count
        ChunkReqs    = $chunkN
        ManifestReqs = $manifestN
        PatchReqs    = $patchN
        Bytes        = $bytes
    }
} | Sort-Object Bytes -Descending

$touchedDepotIds = @($perDepot | ForEach-Object { $_.DepotId })

# --- 7. filesystem check: what SteamPrefill actually wrote to poc/cache/depot ---

function Get-DepotFsCheck {
    <#
        For each depot ID touched by this run (per the log), inspects
        poc/cache/depot/<id>/ on disk: file counts + total bytes under
        chunk/, manifest/, patch/, plus two kinds of layout violations:
          - chunk filenames that are NOT a bare 40-hex-char SHA1
            (docs/PROJECT_PLAN.md section 4's path-faithful expectation)
          - anything stored directly under depot/<id>/ that isn't one of
            the chunk/manifest/patch subfolders
    #>
    param([string]$CacheDepotDir, [string[]]$DepotIds)

    $results = @()
    foreach ($id in $DepotIds) {
        $depotPath = Join-Path $CacheDepotDir $id
        if (-not (Test-Path $depotPath)) {
            $results += [pscustomobject]@{
                DepotId = $id; Found = $false
                ChunkCount = 0; ChunkBytes = 0
                ManifestCount = 0; ManifestBytes = 0
                PatchCount = 0; PatchBytes = 0
                BadChunkNames = @(); UnexpectedEntries = @()
            }
            continue
        }

        $chunkDir    = Join-Path $depotPath "chunk"
        $manifestDir = Join-Path $depotPath "manifest"
        $patchDir    = Join-Path $depotPath "patch"

        $chunkFiles    = @(if (Test-Path $chunkDir)    { Get-ChildItem -Path $chunkDir    -File -Recurse })
        $manifestFiles = @(if (Test-Path $manifestDir) { Get-ChildItem -Path $manifestDir -File -Recurse })
        $patchFiles    = @(if (Test-Path $patchDir)    { Get-ChildItem -Path $patchDir    -File -Recurse })

        $chunkBytes    = ($chunkFiles    | Measure-Object -Property Length -Sum).Sum
        $manifestBytes = ($manifestFiles | Measure-Object -Property Length -Sum).Sum
        $patchBytes    = ($patchFiles    | Measure-Object -Property Length -Sum).Sum

        $badNames = @($chunkFiles | Where-Object { $_.Name -notmatch '^[0-9a-fA-F]{40}$' } | ForEach-Object { $_.FullName })

        $unexpected = @(Get-ChildItem -Path $depotPath -Force |
            Where-Object { $_.Name -notin @('chunk', 'manifest', 'patch') } |
            ForEach-Object { $_.FullName })

        $results += [pscustomobject]@{
            DepotId       = $id
            Found         = $true
            ChunkCount    = $chunkFiles.Count
            ChunkBytes    = $(if ($chunkBytes) { $chunkBytes } else { 0 })
            ManifestCount = $manifestFiles.Count
            ManifestBytes = $(if ($manifestBytes) { $manifestBytes } else { 0 })
            PatchCount    = $patchFiles.Count
            PatchBytes    = $(if ($patchBytes) { $patchBytes } else { 0 })
            BadChunkNames = $badNames
            UnexpectedEntries = $unexpected
        }
    }
    return $results
}

$fsCheck = @(Get-DepotFsCheck -CacheDepotDir $CacheDepotDir -DepotIds $touchedDepotIds)

$allBadChunkNames    = @($fsCheck | ForEach-Object { $_.BadChunkNames } | Where-Object { $_ })
$allUnexpectedEntries = @($fsCheck | ForEach-Object { $_.UnexpectedEntries } | Where-Object { $_ })
$missingDepotDirs    = @($fsCheck | Where-Object { -not $_.Found } | ForEach-Object { $_.DepotId })
$layoutClean = ($allBadChunkNames.Count -eq 0 -and $allUnexpectedEntries.Count -eq 0 -and $missingDepotDirs.Count -eq 0)

# --- 8. assemble result object ---------------------------------------------------

$result = [pscustomobject]@{
    Meta = [pscustomobject]@{
        LogFile        = (Resolve-Path $LogFile).Path
        CacheDepotDir  = $CacheDepotDir
        WindowMode     = $windowMode
        From           = $From
        To             = $To
        BurstInfo      = $burstInfo
        TotalLinesRaw  = $rawLines.Count
        ParsedLines    = $totalAll
        SkippedLines   = $skipped
        WindowLines    = $total
        WindowFrom     = $fromDt
        WindowTo       = $toDt
    }
    NoPrefillTrafficDetected = $noPrefillTrafficDetected
    UriConformance = [pscustomobject]@{
        Total          = $total
        ChunkCount     = $chunkCount
        ManifestCount  = $manifestCount
        PatchCount     = $patchCount
        OtherCount     = $otherCount
        OtherUris      = $otherUriList
    }
    RangeUsage = [pscustomobject]@{
        Total          = $total
        NoneCount      = $rangeNone
        UsedCount      = $rangeUsedTotal
        SuffixCount    = $rangeSuffix
        ExplicitCount  = $rangeExplicit
        MultiCount     = $rangeMulti
        OtherCount     = $rangeOther
    }
    HitMiss = [pscustomobject]@{
        HitCount    = $hitCount
        MissCount   = $missCount
        OtherCount  = $otherCacheEntries.Count
        HitRatioPct = $hitRatio
        HitBytes    = $hitBytes
        MissBytes   = $missBytes
        TotalBytes  = $totalBytes
    }
    PerDepot = $perDepot
    FsCheck  = $fsCheck
    LayoutCrossCheck = [pscustomobject]@{
        Clean              = $layoutClean
        BadChunkNames      = $allBadChunkNames
        UnexpectedEntries  = $allUnexpectedEntries
        MissingDepotDirs   = $missingDepotDirs
    }
}

# --- 9. render report -------------------------------------------------------------

function New-VerifyReport {
    param($Result)

    $lines = New-Object System.Collections.Generic.List[string]
    $add = { param($s) $lines.Add($s) }

    $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    & $add "# SteamVault WP 0.4 - SteamPrefill verification"
    & $add ""
    & $add "Generated: $now"
    & $add "Log file: $($Result.Meta.LogFile)"
    & $add "Cache depot dir: $($Result.Meta.CacheDepotDir)"

    $m = $Result.Meta
    if ($m.WindowMode -eq "explicit") {
        & $add ("Window: explicit From='{0}' To='{1}'" -f $m.From, $m.To)
    }
    elseif ($m.WindowMode -eq "auto-burst") {
        & $add ("Window: auto-detected newest burst (gap threshold {0}s) - {1} burst(s) found in the log, analyzing the last one ({2} entries)" -f `
            $m.BurstInfo.GapSeconds, $m.BurstInfo.TotalBursts, $m.BurstInfo.ChosenCount)
    }
    else {
        & $add "Window: log file is empty - nothing to analyze."
    }
    & $add ("Lines: {0} total in file, {1} parsed, {2} skipped (unparseable), {3} in analyzed window" -f `
        $m.TotalLinesRaw, $m.ParsedLines, $m.SkippedLines, $m.WindowLines)
    if ($m.WindowFrom -and $m.WindowTo) {
        & $add ("Window time span: {0:yyyy-MM-dd HH:mm:ss} .. {1:yyyy-MM-dd HH:mm:ss}" -f $m.WindowFrom, $m.WindowTo)
    }
    & $add ""

    if ($Result.Meta.WindowLines -eq 0) {
        & $add "## Result: NO LOG ENTRIES IN THE ANALYZED WINDOW"
        & $add ""
        & $add "The log has no entries at all in this window. Nothing to report - check you pointed -LogFile at the right file, or that SteamPrefill actually ran."
        return $lines
    }

    if ($Result.NoPrefillTrafficDetected) {
        & $add "## Result: NO STEAMPREFILL / DEPOT TRAFFIC DETECTED IN THIS WINDOW"
        & $add ""
        & $add ('{0} log line(s) fall in this window, but none matched a Steam CDN depot URI scheme (`/depot/<id>/chunk|manifest|patch/...`).' -f $Result.Meta.WindowLines)
        & $add ""
        & $add 'poc/conf/nginx.conf now implements the /lancache-heartbeat + X-LanCache-Processed-By contract SteamPrefill''s auto-detection requires (see PROTOCOL.md section 0), so this most likely means `prefill` was never actually run in this window, aborted before any depot request for another reason, or this window doesn''t cover your run. First, confirm the heartbeat endpoint is live on the currently-running nginx instance:'
        & $add ""
        & $add '    curl.exe -i http://127.0.0.1/lancache-heartbeat'
        & $add ""
        & $add 'Expect HTTP 200 with an X-LanCache-Processed-By header. If that check fails, nginx isn''t running the current config (reload/restart it) or the hosts entry isn''t active - see PROTOCOL.md section 0. If it passes, re-check that you actually ran `prefill` and that this script''s window covers it (try without -From/-To, or pass explicit timestamps).'
        & $add ""
        if ($Result.UriConformance.OtherCount -gt 0) {
            & $add "For reference, the non-depot URIs seen in this window:"
            & $add ""
            & $add "| Count | URI |"
            & $add "|---|---|"
            foreach ($u in $Result.UriConformance.OtherUris) {
                & $add ("| {0} | {1} |" -f $u.Count, $u.Uri)
            }
            & $add ""
        }
        return $lines
    }

    $uc = $Result.UriConformance
    & $add "## 1. URI-scheme conformance (chunk / manifest / patch / other)"
    & $add ""
    & $add "| Category | Count | % of total |"
    & $add "|---|---|---|"
    & $add ("| chunk (/depot/<id>/chunk/<sha1>) | {0} | {1} |" -f $uc.ChunkCount, (Format-Pct $uc.ChunkCount $uc.Total))
    & $add ("| manifest (/depot/<id>/manifest/...) | {0} | {1} |" -f $uc.ManifestCount, (Format-Pct $uc.ManifestCount $uc.Total))
    & $add ("| patch (/depot/<id>/patch/<from>/<to>) | {0} | {1} |" -f $uc.PatchCount, (Format-Pct $uc.PatchCount $uc.Total))
    & $add ("| other / non-conforming | {0} | {1} |" -f $uc.OtherCount, (Format-Pct $uc.OtherCount $uc.Total))
    & $add ("| **Total** | {0} | 100.0% |" -f $uc.Total)
    & $add ""
    if ($uc.OtherUris.Count -eq 0) {
        & $add "No non-conforming URIs observed."
    } else {
        & $add "Non-conforming URIs observed (verbatim, most frequent first):"
        & $add ""
        & $add "| Count | URI |"
        & $add "|---|---|"
        foreach ($u in $uc.OtherUris) {
            & $add ("| {0} | {1} |" -f $u.Count, $u.Uri)
        }
    }
    & $add ""

    $ru = $Result.RangeUsage
    & $add "## 2. Range usage BY STEAMPREFILL (Phase-0: WP 0.3's real Windows client used zero Range requests - does SteamPrefill?)"
    & $add ""
    & $add "| Range kind | Count | % of total |"
    & $add "|---|---|---|"
    & $add ("| none (full-body request) | {0} | {1} |" -f $ru.NoneCount, (Format-Pct $ru.NoneCount $ru.Total))
    & $add ("| suffix (bytes=-N) | {0} | {1} |" -f $ru.SuffixCount, (Format-Pct $ru.SuffixCount $ru.Total))
    & $add ("| explicit (bytes=N-M) | {0} | {1} |" -f $ru.ExplicitCount, (Format-Pct $ru.ExplicitCount $ru.Total))
    & $add ("| multi-range (comma-separated) | {0} | {1} |" -f $ru.MultiCount, (Format-Pct $ru.MultiCount $ru.Total))
    & $add ("| other / malformed | {0} | {1} |" -f $ru.OtherCount, (Format-Pct $ru.OtherCount $ru.Total))
    & $add ("| **Any Range header used** | {0} | {1} |" -f $ru.UsedCount, (Format-Pct $ru.UsedCount $ru.Total))
    & $add ""

    $hm = $Result.HitMiss
    & $add "## 3. Hit/miss split and bytes fetched"
    & $add ""
    & $add "| | Count | Bytes |"
    & $add "|---|---|---|"
    & $add ("| HIT (served from disk) | {0} | {1} |" -f $hm.HitCount, (Format-Bytes $hm.HitBytes))
    & $add ("| MISS (fetched from upstream) | {0} | {1} |" -f $hm.MissCount, (Format-Bytes $hm.MissBytes))
    & $add ("| other (e.g. /health, cache status -) | {0} | - |" -f $hm.OtherCount)
    if ($null -ne $hm.HitRatioPct) {
        & $add ("| **Hit ratio (HIT / (HIT+MISS))** | **{0}%** | |" -f (Format-Num $hm.HitRatioPct "N1"))
    } else {
        & $add "| Hit ratio | n/a (no HIT or MISS requests in window) | |"
    }
    & $add ""

    & $add "## 4. Per-depot request/byte counts (from the log)"
    & $add ""
    if ($Result.PerDepot.Count -eq 0) {
        & $add "No depot-scoped requests in this window."
    } else {
        & $add "| Depot ID | Requests | chunk | manifest | patch | Bytes |"
        & $add "|---|---|---|---|---|---|"
        foreach ($d in $Result.PerDepot) {
            & $add ("| {0} | {1} | {2} | {3} | {4} | {5} |" -f $d.DepotId, $d.Requests, $d.ChunkReqs, $d.ManifestReqs, $d.PatchReqs, (Format-Bytes $d.Bytes))
        }
    }
    & $add ""

    & $add "## 5. Filesystem check (poc/cache/depot/) for each depot this run touched"
    & $add ""
    if ($Result.FsCheck.Count -eq 0) {
        & $add "No depots to check."
    } else {
        & $add "| Depot ID | On disk? | chunk files | chunk bytes | manifest files | manifest bytes | patch files | patch bytes |"
        & $add "|---|---|---|---|---|---|---|---|"
        foreach ($f in $Result.FsCheck) {
            if (-not $f.Found) {
                & $add ("| {0} | **NOT FOUND** | - | - | - | - | - | - |" -f $f.DepotId)
            } else {
                & $add ("| {0} | yes | {1} | {2} | {3} | {4} | {5} | {6} |" -f `
                    $f.DepotId, $f.ChunkCount, (Format-Bytes $f.ChunkBytes), $f.ManifestCount, (Format-Bytes $f.ManifestBytes), $f.PatchCount, (Format-Bytes $f.PatchBytes))
            }
        }
    }
    & $add ""

    $lc = $Result.LayoutCrossCheck
    & $add "## 6. Path-faithful layout cross-check (docs/PROJECT_PLAN.md section 4)"
    & $add ""
    & $add 'Checks: every file under a touched depot''s `chunk/` subfolder must be named as a bare 40-hex-character SHA1 (no extension, no extra path segments), and nothing should be stored directly under a depot''s own directory outside the `chunk/`/`manifest/`/`patch/` subfolders. (`manifest/` and `patch/` contents use Steam''s own numeric manifest/request-code IDs, not SHA1 hashes, so they are counted above but not pattern-checked here.)'
    & $add ""
    if ($lc.Clean) {
        & $add '**PASS** - no layout violations found. Every chunk file matched the expected 40-hex-SHA1 naming verbatim, and no stray files/folders were found directly under any touched depot directory.'
    } else {
        & $add "**Layout issues found:**"
        & $add ""
        if ($lc.MissingDepotDirs.Count -gt 0) {
            & $add ('- Depot ID(s) referenced in the log but with no corresponding directory under `poc/cache/depot/`: {0}' -f ($lc.MissingDepotDirs -join ", "))
        }
        if ($lc.BadChunkNames.Count -gt 0) {
            & $add "- Chunk file(s) NOT matching the expected 40-hex-SHA1 filename pattern, listed verbatim:"
            & $add ""
            foreach ($n in $lc.BadChunkNames) { & $add ('  - `{0}`' -f $n) }
        }
        if ($lc.UnexpectedEntries.Count -gt 0) {
            & $add "- Unexpected entries found directly under a depot directory (outside chunk/manifest/patch), listed verbatim:"
            & $add ""
            foreach ($n in $lc.UnexpectedEntries) { & $add ('  - `{0}`' -f $n) }
        }
    }
    & $add ""

    & $add "## 7. Out of scope for this script"
    & $add ""
    & $add "- The real Windows Steam client (WP 0.3, see poc/steam-client-test/) - not re-exercised here."
    & $add "- Whether SteamPrefill's own lancache-heartbeat detection succeeded is inferred only indirectly (via 'was there any depot traffic at all') - see PROTOCOL.md section 0 for the direct pre-flight check."
    & $add ""

    return $lines
}

$reportLines = New-VerifyReport -Result $result

foreach ($line in $reportLines) {
    if ($line -match '^#') {
        Write-Host $line -ForegroundColor Cyan
    } else {
        Write-Host $line
    }
}

if (-not $NoReport) {
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $outFile = Join-Path $OutDir "RESULTS-STEAMPREFILL-$stamp.md"
    $reportLines -join "`r`n" | Set-Content -Path $outFile -Encoding utf8
    Write-Host ""
    Write-Host "Report written to: $outFile" -ForegroundColor Green
}

if ($PassThru) {
    return $result
}
