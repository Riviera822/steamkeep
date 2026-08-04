<#
.SYNOPSIS
    Phase 0 PoC - WP 0.3: analyzes poc/logs/access.log for real-Steam-client
    evidence and answers the Phase-0 checkboxes (docs/PROJECT_PLAN.md section 7)
    automatically.

.DESCRIPTION
    Mines the WP 0.1 vault access log format (see poc/README.md "Access log
    format") for:
      1. URI-scheme conformance: does the client consistently request
         /depot/<id>/chunk/<hash> (and /depot/<id>/manifest/...)? Every
         non-conforming URI is listed verbatim - that's exactly the
         information PROJECT_PLAN.md section 7 asks us to learn.
      2. Range usage by the real client: how many requests carried a Range
         header, and what kind (suffix / explicit / multi-range).
      3. Hit/miss split (+ bytes) over the whole window and, if -From/-To
         narrow it to a single run, a clean hit ratio + throughput estimate
         for that run.
      4. Data points for the miss-handling decision (section 5 of the plan):
         average latency/throughput of MISS vs. HIT requests.
      5. Per-depot request/byte counts - the foundation for the later
         depot -> app mapping sanity check.

    Robust to a log that also contains earlier WP 0.1/0.2 traffic: unrelated
    lines just become part of the (unfiltered) totals, and -From/-To can
    narrow the analysis to a specific run's wall-clock window.

    Every console section is also written to
    poc/steam-client-test/RESULTS-<timestamp>.md, verbatim, so the write-up
    doesn't need to be transcribed by hand.

.PARAMETER LogFile
    Path to the vault access log. Defaults to poc/logs/access.log relative
    to this script.

.PARAMETER From
    Optional lower bound (inclusive), parsed with [datetime]::Parse, e.g.
    "2026-08-04 18:05:00". Compared against the log's local wall-clock
    timestamp ($time_local) - no timezone conversion is attempted, since
    both the log and any time you'd type here come from the same machine.

.PARAMETER To
    Optional upper bound (inclusive), same format as -From.

.PARAMETER OutDir
    Directory the RESULTS-<timestamp>.md file is written to. Defaults to
    this script's own directory.

.PARAMETER NoReport
    Skip writing the RESULTS-<timestamp>.md file (console output only).
    Used by test-analyze.ps1 so repeated test runs don't litter the folder.

.PARAMETER PassThru
    Also emit the computed result as a PowerShell object on the pipeline
    (in addition to the console/markdown report), so it can be asserted on
    programmatically. Used by test-analyze.ps1.

.EXAMPLE
    .\analyze.ps1
    Analyzes the whole log, writes RESULTS-<timestamp>.md.

.EXAMPLE
    .\analyze.ps1 -From "2026-08-04 18:05:00" -To "2026-08-04 18:12:00"
    Analyzes only the second (cache-warm) run's window.
#>

[CmdletBinding()]
param(
    [string]$LogFile  = (Join-Path $PSScriptRoot "..\logs\access.log"),
    [string]$From     = "",
    [string]$To       = "",
    [string]$OutDir   = $PSScriptRoot,
    [switch]$NoReport,
    [switch]$PassThru
)

$ErrorActionPreference = "Stop"

# --- log line parsing --------------------------------------------------------
# Matches the "vault" log_format in poc/conf/nginx.conf:
#   $time_local uri="$request_uri" status=$status range="$http_range"
#   upstream_status=$vault_upstream_status bytes_sent=$bytes_sent
#   request_time=$request_time cache=$vault_cache_status
$script:LinePattern = '^(?<time>\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}) ' +
    'uri="(?<uri>[^"]*)" status=(?<status>\d+) range="(?<range>[^"]*)" ' +
    'upstream_status=(?<ustatus>\S+) bytes_sent=(?<bytes>\d+) ' +
    'request_time=(?<rtime>[0-9.]+) cache=(?<cache>\S+)$'

$script:MonthNumbers = @{
    Jan = 1; Feb = 2; Mar = 3; Apr = 4; May = 5; Jun = 6
    Jul = 7; Aug = 8; Sep = 9; Oct = 10; Nov = 11; Dec = 12
}

function ConvertTo-VaultDateTime {
    <#
        Parses nginx's $time_local (e.g. "04/Aug/2026:18:05:00 +0200") into a
        plain [datetime] using the wall-clock fields only (day/month/year/
        time) - the UTC offset is not applied, so this matches naively-typed
        -From/-To values on the same machine. Returns $null on failure.
    #>
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
    <#
        Parses one access-log line into a structured object, or returns $null
        if the line doesn't match the expected format (kept lenient on
        purpose - unrelated/garbled lines are skipped, not fatal).
    #>
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
        Classifies a request URI against the expected Steam CDN scheme
        (docs/PROJECT_PLAN.md section 7): chunk, manifest, or other
        (non-conforming - the interesting case to report verbatim).
    #>
    param([string]$Uri)

    if ($Uri -match '^/depot/(?<depot>\d+)/chunk/(?<hash>[0-9a-fA-F]{40})$') {
        return [pscustomobject]@{ Class = 'chunk'; DepotId = $Matches['depot'] }
    }
    if ($Uri -match '^/depot/(?<depot>\d+)/manifest/') {
        return [pscustomobject]@{ Class = 'manifest'; DepotId = $Matches['depot'] }
    }
    return [pscustomobject]@{ Class = 'other'; DepotId = $null }
}

function Get-RangeClass {
    <#
        Classifies the $http_range value logged for a request: none, suffix
        (bytes=-500), explicit (bytes=100-200 or bytes=100-), multi
        (comma-separated), or other (malformed / unexpected).
    #>
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
    # Locale-independent numeric formatting (this machine's locale uses a
    # comma decimal separator; the report must read the same on every
    # machine/regardless of who runs it, so InvariantCulture is forced here
    # rather than relying on -f's current-culture default).
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

# --- 1. load + parse ---------------------------------------------------------

if (-not (Test-Path $LogFile)) {
    throw "Log file not found: $LogFile (run the PoC and generate some traffic first - see PROTOCOL.md)"
}

$rawLines = Get-Content $LogFile
$parsed = @()
$skipped = 0
foreach ($line in $rawLines) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $entry = ConvertFrom-VaultLogLine $line
    if ($entry) { $parsed += $entry } else { $skipped++ }
}

# --- 2. optional time-window filter -----------------------------------------

$fromDt = $null
$toDt = $null
if ($From -ne "") {
    try { $fromDt = [datetime]::Parse($From) } catch { throw "Could not parse -From '$From': $_" }
}
if ($To -ne "") {
    try { $toDt = [datetime]::Parse($To) } catch { throw "Could not parse -To '$To': $_" }
}

$windowed = $parsed
if ($fromDt) { $windowed = $windowed | Where-Object { $_.Time -ge $fromDt } }
if ($toDt)   { $windowed = $windowed | Where-Object { $_.Time -le $toDt } }
$windowed = @($windowed)

$totalAll = $parsed.Count
$total = $windowed.Count

# --- 3. URI-scheme conformance -----------------------------------------------

$classified = $windowed | ForEach-Object {
    $info = Get-UriInfo $_.Uri
    [pscustomobject]@{ Entry = $_; Class = $info.Class; DepotId = $info.DepotId }
}

$chunkCount    = @($classified | Where-Object { $_.Class -eq 'chunk' }).Count
$manifestCount = @($classified | Where-Object { $_.Class -eq 'manifest' }).Count
$otherEntries  = @($classified | Where-Object { $_.Class -eq 'other' })
$otherCount    = $otherEntries.Count

$otherUriList = $otherEntries | Group-Object { $_.Entry.Uri } |
    Sort-Object -Property @{ Expression = 'Count'; Descending = $true }, @{ Expression = 'Name'; Descending = $false } |
    ForEach-Object { [pscustomobject]@{ Uri = $_.Name; Count = $_.Count } }

# --- 4. range usage -----------------------------------------------------------

$rangeClasses = $windowed | ForEach-Object { Get-RangeClass $_.Range }
$rangeNone     = @($rangeClasses | Where-Object { $_ -eq 'none' }).Count
$rangeSuffix   = @($rangeClasses | Where-Object { $_ -eq 'suffix' }).Count
$rangeExplicit = @($rangeClasses | Where-Object { $_ -eq 'explicit' }).Count
$rangeMulti    = @($rangeClasses | Where-Object { $_ -eq 'multi' }).Count
$rangeOther    = @($rangeClasses | Where-Object { $_ -eq 'other' }).Count
$rangeUsedTotal = $total - $rangeNone

# --- 5. hit/miss split + bytes -------------------------------------------------

$hitEntries   = @($windowed | Where-Object { $_.Cache -eq 'HIT' })
$missEntries  = @($windowed | Where-Object { $_.Cache -eq 'MISS' })
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

# --- 6. throughput estimate for this window (intended for a cache-warm run) --

$minTime = if ($total -gt 0) { ($windowed | Measure-Object -Property Time -Minimum).Minimum } else { $null }
$maxTime = if ($total -gt 0) { ($windowed | Measure-Object -Property Time -Maximum).Maximum } else { $null }
$wallSeconds = if ($minTime -and $maxTime) { ($maxTime - $minTime).TotalSeconds } else { 0 }
$throughputBps = if ($wallSeconds -gt 0) { $totalBytes / $wallSeconds } else { $null }

# average latency (request_time) split HIT vs MISS - input for the
# miss-handling decision (synchronous store vs async prefill)
$avgHitTime  = if ($hitCount  -gt 0) { ($hitEntries  | Measure-Object -Property RequestTime -Average).Average } else { $null }
$avgMissTime = if ($missCount -gt 0) { ($missEntries | Measure-Object -Property RequestTime -Average).Average } else { $null }
$avgMissBytes = if ($missCount -gt 0) { $missBytes / $missCount } else { $null }
$avgHitBytes  = if ($hitCount  -gt 0) { $hitBytes  / $hitCount  } else { $null }
$missThroughputBps = if ($avgMissTime -and $avgMissTime -gt 0 -and $avgMissBytes) { $avgMissBytes / $avgMissTime } else { $null }
$hitThroughputBps  = if ($avgHitTime  -and $avgHitTime  -gt 0 -and $avgHitBytes)  { $avgHitBytes  / $avgHitTime  } else { $null }

# --- 7. per-depot request/byte counts -----------------------------------------

$perDepot = $classified | Where-Object { $_.DepotId } | Group-Object DepotId | ForEach-Object {
    $bytes = ($_.Group | ForEach-Object { $_.Entry.BytesSent } | Measure-Object -Sum).Sum
    $chunkN = @($_.Group | Where-Object { $_.Class -eq 'chunk' }).Count
    $manifestN = @($_.Group | Where-Object { $_.Class -eq 'manifest' }).Count
    [pscustomobject]@{
        DepotId      = $_.Name
        Requests     = $_.Count
        ChunkReqs    = $chunkN
        ManifestReqs = $manifestN
        Bytes        = $bytes
    }
} | Sort-Object Bytes -Descending

# --- 8. assemble result object -------------------------------------------------

$result = [pscustomobject]@{
    Meta = [pscustomobject]@{
        LogFile       = (Resolve-Path $LogFile).Path
        From          = $From
        To            = $To
        TotalLinesRaw = $rawLines.Count
        ParsedLines   = $totalAll
        SkippedLines  = $skipped
        WindowLines   = $total
        MinTime       = $minTime
        MaxTime       = $maxTime
    }
    UriConformance = [pscustomobject]@{
        Total          = $total
        ChunkCount     = $chunkCount
        ManifestCount  = $manifestCount
        OtherCount     = $otherCount
        ChunkPct       = if ($total -gt 0) { 100.0 * $chunkCount / $total } else { $null }
        ManifestPct    = if ($total -gt 0) { 100.0 * $manifestCount / $total } else { $null }
        OtherPct       = if ($total -gt 0) { 100.0 * $otherCount / $total } else { $null }
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
        UsedPct        = if ($total -gt 0) { 100.0 * $rangeUsedTotal / $total } else { $null }
    }
    HitMiss = [pscustomobject]@{
        HitCount     = $hitCount
        MissCount    = $missCount
        OtherCount   = $otherCacheEntries.Count
        HitRatioPct  = $hitRatio
        HitBytes     = $hitBytes
        MissBytes    = $missBytes
        TotalBytes   = $totalBytes
    }
    Throughput = [pscustomobject]@{
        WallSeconds       = $wallSeconds
        TotalBytes        = $totalBytes
        ThroughputBps     = $throughputBps
        AvgHitRequestTime  = $avgHitTime
        AvgMissRequestTime = $avgMissTime
        HitThroughputBps   = $hitThroughputBps
        MissThroughputBps  = $missThroughputBps
    }
    PerDepot = $perDepot
}

# --- 9. render report (markdown, also used verbatim for console output) ------

function New-VaultReport {
    param($Result, [string]$From, [string]$To)

    $lines = New-Object System.Collections.Generic.List[string]
    $add = { param($s) $lines.Add($s) }

    $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    & $add "# SteamVault WP 0.3 - Real-Steam-client log analysis"
    & $add ""
    & $add "Generated: $now"
    & $add "Log file: $($Result.Meta.LogFile)"
    $windowDesc = if ($From -or $To) { "From='$From' To='$To'" } else { "(no window - entire log)" }
    & $add "Window: $windowDesc"
    & $add ("Lines: {0} total in file, {1} parsed, {2} skipped (unparseable), {3} in window" -f `
        $Result.Meta.TotalLinesRaw, $Result.Meta.ParsedLines, $Result.Meta.SkippedLines, $Result.Meta.WindowLines)
    if ($Result.Meta.MinTime -and $Result.Meta.MaxTime) {
        & $add ("Window time span: {0:yyyy-MM-dd HH:mm:ss} .. {1:yyyy-MM-dd HH:mm:ss}" -f $Result.Meta.MinTime, $Result.Meta.MaxTime)
    }
    & $add ""

    $uc = $Result.UriConformance
    & $add "## 1. URI-scheme conformance (Phase-0: does the client consistently use /depot/<id>/chunk/<hash>?)"
    & $add ""
    if ($uc.Total -eq 0) {
        & $add "No requests in this window."
    } else {
        & $add "| Category | Count | % of total |"
        & $add "|---|---|---|"
        & $add ("| chunk (/depot/<id>/chunk/<sha1>) | {0} | {1} |" -f $uc.ChunkCount, (Format-Pct $uc.ChunkCount $uc.Total))
        & $add ("| manifest (/depot/<id>/manifest/...) | {0} | {1} |" -f $uc.ManifestCount, (Format-Pct $uc.ManifestCount $uc.Total))
        & $add ("| other / non-conforming | {0} | {1} |" -f $uc.OtherCount, (Format-Pct $uc.OtherCount $uc.Total))
        & $add ("| **Total** | {0} | 100.0% |" -f $uc.Total)
        & $add ""
        if ($uc.OtherUris.Count -eq 0) {
            & $add "No non-conforming URIs observed - every request matched the expected chunk/manifest scheme."
        } else {
            & $add "Non-conforming URIs observed (verbatim, most frequent first) - **this is exactly the information Phase 0 asks us to learn about the real client's request scheme**:"
            & $add ""
            & $add "| Count | URI |"
            & $add "|---|---|"
            foreach ($u in $uc.OtherUris) {
                & $add ("| {0} | {1} |" -f $u.Count, $u.Uri)
            }
        }
    }
    & $add ""

    $ru = $Result.RangeUsage
    & $add "## 2. Range usage by the real client (Phase-0: do range requests work cleanly with proxy_store?)"
    & $add ""
    if ($ru.Total -eq 0) {
        & $add "No requests in this window."
    } else {
        & $add "| Range kind | Count | % of total |"
        & $add "|---|---|---|"
        & $add ("| none (full-body request) | {0} | {1} |" -f $ru.NoneCount, (Format-Pct $ru.NoneCount $ru.Total))
        & $add ("| suffix (bytes=-N) | {0} | {1} |" -f $ru.SuffixCount, (Format-Pct $ru.SuffixCount $ru.Total))
        & $add ("| explicit (bytes=N-M) | {0} | {1} |" -f $ru.ExplicitCount, (Format-Pct $ru.ExplicitCount $ru.Total))
        & $add ("| multi-range (comma-separated) | {0} | {1} |" -f $ru.MultiCount, (Format-Pct $ru.MultiCount $ru.Total))
        & $add ("| other / malformed | {0} | {1} |" -f $ru.OtherCount, (Format-Pct $ru.OtherCount $ru.Total))
        & $add ("| **Any Range header used** | {0} | {1} |" -f $ru.UsedCount, (Format-Pct $ru.UsedCount $ru.Total))
        & $add ""
        & $add "Cross-reference with poc/RANGE-FINDINGS.md (WP 0.2, synthetic curl-based evidence) to see whether the real client's Range usage matches the assumptions tested there."
    }
    & $add ""

    $hm = $Result.HitMiss
    & $add "## 3. Hit/miss split and bytes served (Phase-0: cache hit on second download? speed LAN-limited?)"
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
    & $add "Tip: pass -From/-To to narrow this to exactly the second (post-uninstall) run's wall-clock window for a clean hit-ratio reading uncontaminated by the first run's misses."
    & $add ""

    $tp = $Result.Throughput
    & $add "## 4. Throughput estimate for this window"
    & $add ""
    & $add ("Wall-clock span: {0} s | Total bytes: {1} | Estimated throughput: {2}" -f `
        (Format-Num $tp.WallSeconds "N1"), (Format-Bytes $tp.TotalBytes), $(if ($tp.ThroughputBps) { "$(Format-Bytes $tp.ThroughputBps)/s" } else { "n/a (span too short or no data)" }))
    & $add ""
    & $add "### Miss-handling decision inputs (Phase-0/Plan section 5: synchronous store vs. async prefill)"
    & $add ""
    & $add "| | avg request_time (s) | avg bytes/request | implied throughput |"
    & $add "|---|---|---|---|"
    & $add ("| MISS (upstream fetch + store) | {0} | {1} | {2} |" -f `
        $(if ($tp.AvgMissRequestTime) { Format-Num $tp.AvgMissRequestTime "N3" } else { "n/a" }), `
        $(if ($avgMissBytes) { Format-Bytes $avgMissBytes } else { "n/a" }), `
        $(if ($tp.MissThroughputBps) { "$(Format-Bytes $tp.MissThroughputBps)/s" } else { "n/a" }))
    & $add ("| HIT (disk read) | {0} | {1} | {2} |" -f `
        $(if ($tp.AvgHitRequestTime) { Format-Num $tp.AvgHitRequestTime "N3" } else { "n/a" }), `
        $(if ($avgHitBytes) { Format-Bytes $avgHitBytes } else { "n/a" }), `
        $(if ($tp.HitThroughputBps) { "$(Format-Bytes $tp.HitThroughputBps)/s" } else { "n/a" }))
    & $add ""
    & $add "This table is data, not the verdict - the plan (section 5 / pain-points table) still needs a human call on synchronous-store-on-miss vs. transparent-passthrough-plus-async-prefill. Use it as the comparison input."
    & $add ""

    & $add "## 5. Per-depot request/byte counts (foundation for later depot->app mapping sanity check)"
    & $add ""
    if ($Result.PerDepot.Count -eq 0) {
        & $add "No depot-scoped requests in this window."
    } else {
        & $add "| Depot ID | Requests | chunk reqs | manifest reqs | Bytes |"
        & $add "|---|---|---|---|---|"
        foreach ($d in $Result.PerDepot) {
            & $add ("| {0} | {1} | {2} | {3} | {4} |" -f $d.DepotId, $d.Requests, $d.ChunkReqs, $d.ManifestReqs, (Format-Bytes $d.Bytes))
        }
    }
    & $add ""

    & $add "## 6. Out of scope for this script"
    & $add ""
    & $add "- SteamPrefill (WP 0.4) - not exercised here."
    & $add "- Linux/Steam Deck client hosts-file behavior - this test kit targets the Windows client only (see PROTOCOL.md)."
    & $add ""

    return $lines
}

$reportLines = New-VaultReport -Result $result -From $From -To $To

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
    $outFile = Join-Path $OutDir "RESULTS-$stamp.md"
    $reportLines -join "`r`n" | Set-Content -Path $outFile -Encoding utf8
    Write-Host ""
    Write-Host "Report written to: $outFile" -ForegroundColor Green
}

if ($PassThru) {
    return $result
}
