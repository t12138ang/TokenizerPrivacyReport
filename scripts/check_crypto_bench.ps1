[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Log = Join-Path $ProjectRoot 'logs\final\check_crypto_bench.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    & $Python -u -m src.crypto.summarize_benchmark --config (Join-Path $ProjectRoot 'configs\final_study.json') 2>&1 | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "Crypto benchmark check exited with code $LASTEXITCODE" }
    Write-Host ('Crypto check elapsed: ' + $Stopwatch.Elapsed)
}
finally {
    Pop-Location
    $Stopwatch.Stop()
}
