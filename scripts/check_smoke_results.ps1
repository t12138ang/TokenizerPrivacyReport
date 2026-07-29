[CmdletBinding()]
param(
    [string]$MetricsPath
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CheckLogPath = Join-Path $ProjectRoot 'logs\check_smoke_results.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
if ([string]::IsNullOrWhiteSpace($MetricsPath)) {
    $MetricsPath = Join-Path $ProjectRoot 'results\smoke\metrics.json'
}

function Write-CheckLine {
    param([string]$Message)
    $Line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o), $Stopwatch.Elapsed.ToString(), $Message)
    Write-Output $Line
    Add-Content -LiteralPath $CheckLogPath -Value $Line -Encoding utf8
}

New-Item -ItemType Directory -Path (Split-Path -Parent $CheckLogPath) -Force | Out-Null
Write-CheckLine '[check stage 1/2] locating and parsing metrics.json'

if (-not (Test-Path -LiteralPath $MetricsPath)) {
    Write-CheckLine 'Run successful: no (metrics.json is missing)'
    exit 1
}

try {
    $Metrics = Get-Content -LiteralPath $MetricsPath -Raw -Encoding utf8 | ConvertFrom-Json
    $Required = @(
        $Metrics.status,
        $Metrics.data.dataset_count,
        $Metrics.data.document_count,
        $Metrics.tokenizers.target.actual_vocab_size,
        $Metrics.parameters.seed,
        $Metrics.tokenizers.target.count,
        $Metrics.tokenizers.shadow.count,
        $Metrics.attack.method,
        $Metrics.attack.score,
        $Metrics.performance.peak_memory_mib,
        $Metrics.performance.elapsed_seconds,
        $Metrics.logging.error_count,
        $Metrics.logging.warning_count
    )
    if ($Required -contains $null) {
        throw 'metrics.json is missing one or more required fields'
    }

    Write-CheckLine '[check stage 2/2] required fields validated; summary follows'
    Write-CheckLine ('Run successful: ' + $(if ($Metrics.status -eq 'success') { 'yes' } else { 'no' }))
    Write-CheckLine ('Data volume: {0} datasets / {1} documents' -f $Metrics.data.dataset_count, $Metrics.data.document_count)
    Write-CheckLine ('Vocabulary size: requested {0} / actual {1}' -f $Metrics.tokenizers.target.requested_vocab_size, $Metrics.tokenizers.target.actual_vocab_size)
    Write-CheckLine ('Random seed: ' + $Metrics.parameters.seed)
    Write-CheckLine ('Target tokenizer count: ' + $Metrics.tokenizers.target.count)
    Write-CheckLine ('Shadow tokenizer count: ' + $Metrics.tokenizers.shadow.count)
    Write-CheckLine ('Attack method: ' + $Metrics.attack.method)
    Write-CheckLine ('Attack score ({0}): {1:N6}' -f $Metrics.attack.score_name, [double]$Metrics.attack.score)
    Write-CheckLine ('Peak memory: {0:N3} MiB' -f [double]$Metrics.performance.peak_memory_mib)
    Write-CheckLine ('Total elapsed: {0:N3} seconds' -f [double]$Metrics.performance.elapsed_seconds)
    Write-CheckLine ('Error count: ' + $Metrics.logging.error_count)
    Write-CheckLine ('Warning count: ' + $Metrics.logging.warning_count)
    Write-CheckLine ('CHECK TOTAL ELAPSED: ' + $Stopwatch.Elapsed.ToString())

    if ($Metrics.status -ne 'success') {
        exit 1
    }
}
catch {
    $FullError = $_ | Out-String
    Write-CheckLine ('Run successful: no (result parse/validation failed; full error follows): ' + $FullError.TrimEnd())
    exit 1
}
