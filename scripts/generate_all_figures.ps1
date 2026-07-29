[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Config = Join-Path $ProjectRoot 'configs\final_study.json'
$Log = Join-Path $ProjectRoot 'logs\final\report_generation.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
function Invoke-Stage([string]$Name, [scriptblock]$Command) {
    $line = ('{0} | elapsed={1} | stage={2}' -f (Get-Date -Format o),$Stopwatch.Elapsed,$Name)
    Write-Host $line
    Add-Content -LiteralPath $Log -Value $line -Encoding utf8
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name exited with code $LASTEXITCODE" }
}
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'results\final\attack_summary.json'))) {
        Invoke-Stage 'summarize-final-attacks' { & $Python -u -m src.summarize_final_attacks --config $Config }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'results\final\crypto\benchmark_summary.json'))) {
        Invoke-Stage 'summarize-2048-bit-paillier' { & $Python -u -m src.crypto.summarize_benchmark --config $Config }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'results\final\result_registry.json'))) {
        Invoke-Stage 'collect-statistics-and-paired-bootstrap' { & $Python -u -m src.reporting.collect_results --config $Config }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'results\final\figures\figure_manifest.json'))) {
        Invoke-Stage 'generate-12-pdf-png-figures' { & $Python -u -m src.reporting.generate_figures --config $Config }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'results\final\tables\latex_generation_manifest.json'))) {
        Invoke-Stage 'generate-latex-macros-and-tables' { & $Python -u -m src.reporting.generate_latex --config $Config }
    }
    Invoke-Stage 'compile-check' { & $Python -m compileall -q src tests }
    Write-Host ('SUCCESS: figures/tables generated; total elapsed=' + $Stopwatch.Elapsed)
}
catch {
    Add-Content -LiteralPath $Log -Value (($_ | Out-String).TrimEnd()) -Encoding utf8
    throw
}
finally {
    Pop-Location
    $Stopwatch.Stop()
}
