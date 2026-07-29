[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Config = Join-Path $ProjectRoot 'configs\final_study.json'
$Log = Join-Path $ProjectRoot 'logs\final\fast_report_generation.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Invoke-Stage([string]$Name, [scriptblock]$Command) {
    $line = ('{0} | elapsed={1} | stage={2}' -f (Get-Date -Format o), $Stopwatch.Elapsed, $Name)
    Write-Host $line
    Add-Content -LiteralPath $Log -Value $line -Encoding utf8
    & $Command 2>&1 | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "$Name exited with code $LASTEXITCODE" }
}

New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
if (-not (Test-Path -LiteralPath $Python)) { throw "Python executable not found: $Python" }
Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath '.\results\final\report_fast\fast_crypto_state.json')) {
        Invoke-Stage 'extract-measured-and-fit-missing-crypto' {
            & $Python -u -m src.reporting.fast_crypto --config $Config --bootstrap-iterations 2000 --seed 20260729
        }
    }
    if (-not (Test-Path -LiteralPath '.\results\final\result_registry.json')) {
        Invoke-Stage 'collect-formal-noncrypto-and-fast-crypto-tables' {
            & $Python -u -m src.reporting.collect_results --config $Config --fast-report
        }
    }
    if (-not (Test-Path -LiteralPath '.\results\final\figures\figure_manifest.json')) {
        Invoke-Stage 'generate-twenty-report-figures' {
            & $Python -u -m src.reporting.generate_figures --config $Config --fast-report
        }
    }
    if (-not (Test-Path -LiteralPath '.\results\final\tables\latex_generation_manifest.json')) {
        Invoke-Stage 'generate-latex-macros-and-tables' {
            & $Python -u -m src.reporting.generate_latex --config $Config --fast-report
        }
    }
    if (-not (Test-Path -LiteralPath '.\README_REPORT.md')) {
        Invoke-Stage 'generate-report-readme-from-machine-results' {
            & $Python -u -m src.reporting.generate_fast_readme --config $Config
        }
    }
    Invoke-Stage 'compile-python-sources' { & $Python -m compileall -q src tests }
    Write-Host ('SUCCESS: fast report derivation complete; total elapsed=' + $Stopwatch.Elapsed)
}
catch {
    Add-Content -LiteralPath $Log -Value (($_ | Out-String).TrimEnd()) -Encoding utf8
    throw
}
finally {
    Pop-Location
    $Stopwatch.Stop()
}
