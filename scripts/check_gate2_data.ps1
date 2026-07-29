[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ValidationPath = Join-Path $ProjectRoot 'data\gate2\validation.json'
$StatsPath = Join-Path $ProjectRoot 'data\gate2\corpus\collection_stats.json'
$LogPath = Join-Path $ProjectRoot 'logs\gate2\check_data.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Gate2DataCheck {
    param([string]$Message)
    $Line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o), $Stopwatch.Elapsed.ToString(), $Message)
    Write-Output $Line
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding utf8
}

New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
try {
    Write-Gate2DataCheck '[Gate2 data check 1/2] parse outputs'
    if (-not (Test-Path -LiteralPath $ValidationPath)) { throw "Missing validation: $ValidationPath" }
    if (-not (Test-Path -LiteralPath $StatsPath)) { throw "Missing collection stats: $StatsPath" }
    $Validation = Get-Content -LiteralPath $ValidationPath -Raw -Encoding utf8 | ConvertFrom-Json
    $Stats = Get-Content -LiteralPath $StatsPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($Validation.status -ne 'success') { throw 'Validation status is not success' }
    Write-Gate2DataCheck '[Gate2 data check 2/2] summary'
    Write-Gate2DataCheck ('Dataset revision: ' + $Validation.dataset_revision)
    Write-Gate2DataCheck ('Corpus SHA-256: ' + $Validation.corpus_sha256)
    Write-Gate2DataCheck ('Scanned C4 records: ' + $Stats.pass1.scanned_records)
    Write-Gate2DataCheck ('Sites/texts: {0}/{1}' -f $Validation.site_count,$Validation.text_count)
    Write-Gate2DataCheck ('Texts per site min/max: {0}/{1}' -f $Validation.site_text_counts.minimum,$Validation.site_text_counts.maximum)
    $ExactWithin = if ($null -eq $Validation.duplicate_and_anomaly_counts.exact_duplicate_within) { 0 } else { $Validation.duplicate_and_anomaly_counts.exact_duplicate_within }
    $ExactCross = if ($null -eq $Validation.duplicate_and_anomaly_counts.exact_duplicate_cross) { 0 } else { $Validation.duplicate_and_anomaly_counts.exact_duplicate_cross }
    $NormalizedWithin = if ($null -eq $Validation.duplicate_and_anomaly_counts.normalized_duplicate_within) { 0 } else { $Validation.duplicate_and_anomaly_counts.normalized_duplicate_within }
    $NormalizedCross = if ($null -eq $Validation.duplicate_and_anomaly_counts.normalized_duplicate_cross) { 0 } else { $Validation.duplicate_and_anomaly_counts.normalized_duplicate_cross }
    Write-Gate2DataCheck ('Exact duplicates within/cross: {0}/{1}' -f $ExactWithin,$ExactCross)
    Write-Gate2DataCheck ('Normalized duplicates within/cross: {0}/{1}' -f $NormalizedWithin,$NormalizedCross)
    Write-Gate2DataCheck ('Full URL fields/URLs in text/site IDs in text: {0}/{1}/{2}' -f $Validation.forbidden_full_url_field_count,$Validation.url_in_text_count,$Validation.site_id_in_text_count)
    Write-Gate2DataCheck ('Validation errors: ' + $Validation.errors.Count)
    Write-Gate2DataCheck ('TOTAL ELAPSED: ' + $Stopwatch.Elapsed.ToString())
}
catch {
    $FullError = $_ | Out-String
    Write-Gate2DataCheck ('ERROR: ' + $FullError.TrimEnd())
    exit 1
}
