[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ValidationPath = Join-Path $ProjectRoot 'data\final\validation.json'
$StatsPath = Join-Path $ProjectRoot 'data\final\corpus\collection_stats.json'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
try {
    if (-not (Test-Path -LiteralPath $ValidationPath -PathType Leaf)) { throw "Missing $ValidationPath" }
    if (-not (Test-Path -LiteralPath $StatsPath -PathType Leaf)) { throw "Missing $StatsPath" }
    $validation = Get-Content -LiteralPath $ValidationPath -Raw -Encoding utf8 | ConvertFrom-Json
    $stats = Get-Content -LiteralPath $StatsPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($validation.status -ne 'success') { throw 'Final data validation is not successful' }
    if ($validation.errors.Count -ne 0) { throw 'Final data validation contains errors' }
    Write-Host ('Status: {0}' -f $validation.status)
    Write-Host ('Revision: {0}' -f $validation.dataset_revision)
    Write-Host ('Corpus SHA-256: {0}' -f $validation.corpus_sha256)
    Write-Host ('Scanned records: {0}' -f $stats.pass1.scan_limit)
    Write-Host ('Development/Main sites: {0}/{1}' -f $validation.scale_site_counts.development,$validation.scale_site_counts.main)
    Write-Host ('Sites/texts: {0}/{1}' -f $validation.site_count,$validation.text_count)
    Write-Host ('Texts per site min/mean/max: {0}/{1:N3}/{2}' -f $validation.texts_per_site.minimum,$validation.texts_per_site.mean,$validation.texts_per_site.maximum)
    Write-Host ('Validation errors: {0}' -f $validation.errors.Count)
}
catch {
    Write-Error ($_ | Out-String)
    exit 1
}
finally {
    $Stopwatch.Stop()
    Write-Host ('TOTAL ELAPSED: {0}' -f $Stopwatch.Elapsed)
}
