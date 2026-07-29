[CmdletBinding()]
param(
    [string]$EnvironmentPrefix
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CondaExe = 'D:\anaconda\Scripts\conda.exe'
$LogPath = Join-Path $ProjectRoot 'logs\smoke_test.log'
$MetricsPath = Join-Path $ProjectRoot 'results\smoke\metrics.json'
$ConfigPath = Join-Path $ProjectRoot 'configs\smoke.json'
$PythonScript = Join-Path $ProjectRoot 'src\smoke_test.py'
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}

$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Stage {
    param([string]$Message)
    $Line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o), $Stopwatch.Elapsed.ToString(), $Message)
    Write-Host $Line
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding utf8
}

New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null

try {
    Write-Stage '[PowerShell stage 1/3] preflight'
    if (-not (Test-Path -LiteralPath $CondaExe)) {
        throw "Conda executable not found: $CondaExe"
    }
    if (-not (Test-Path -LiteralPath $EnvironmentPrefix)) {
        throw "Independent Conda environment not found: $EnvironmentPrefix"
    }
    if (Test-Path -LiteralPath $MetricsPath) {
        throw "Refusing to overwrite existing result: $MetricsPath"
    }

    $SmokeConfig = Get-Content -LiteralPath $ConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
    $env:PYTHONHASHSEED = [string]$SmokeConfig.seed
    $env:TOKENIZERS_PARALLELISM = 'false'

    Write-Stage '[PowerShell stage 2/3] launching unbuffered Python smoke test'
    & $CondaExe run --no-capture-output --prefix $EnvironmentPrefix python -u $PythonScript --config $ConfigPath --output $MetricsPath --log $LogPath
    if ($LASTEXITCODE -ne 0) {
        throw "Smoke test process exited with code $LASTEXITCODE"
    }

    Write-Stage '[PowerShell stage 3/3] validating generated result'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'check_smoke_results.ps1') -MetricsPath $MetricsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Result validation exited with code $LASTEXITCODE"
    }
    Write-Stage 'SUCCESS: smoke test and validation completed'
}
catch {
    $ErrorText = $_ | Out-String
    Write-Stage ("ERROR: " + $ErrorText.TrimEnd())
    throw
}
finally {
    $Stopwatch.Stop()
    Write-Stage ('TOTAL ELAPSED: ' + $Stopwatch.Elapsed.ToString())
}
