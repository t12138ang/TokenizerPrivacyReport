[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Log = Join-Path $ProjectRoot 'logs\final\crypto_bench.log'
$WrapperLog = Join-Path $ProjectRoot 'logs\final\crypto_wrapper.log'
$Config = Join-Path $ProjectRoot 'configs\final_study.json'
$Study = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$FullConfig = Join-Path $ProjectRoot 'configs\crypto_full.json'
$Smoke = Join-Path $ProjectRoot 'results\final\crypto\correctness_smoke_1024.json'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
function Write-Stage([string]$Message) {
    $line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o),$Stopwatch.Elapsed,$Message)
    Write-Host $line
    Add-Content -LiteralPath $WrapperLog -Value $line -Encoding utf8
}
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    $env:PYTHONHASHSEED = [string]$Study.seeds[0]
    Write-Stage '[Crypto 1/3] 1024-bit development-only correctness smoke'
    & $Python -u -m src.crypto.correctness_smoke --output $Smoke
    if ($LASTEXITCODE -ne 0) { throw "Crypto correctness smoke exited with code $LASTEXITCODE" }
    Write-Stage '[Crypto 2/3] real 2048-bit matrix: 5 warmups + 20 measured repetitions per cell'
    & $Python -u -m src.crypto.benchmark --config $Config --log $Log
    if ($LASTEXITCODE -ne 0) { throw "Formal crypto benchmark exited with code $LASTEXITCODE" }
    Write-Stage '[Crypto 3/3] actual complete Development 4k SA-DP-BPE with 2048-bit Paillier'
    & $Python -u -m src.crypto.full_tokenizer_benchmark --config $FullConfig --log $Log
    if ($LASTEXITCODE -ne 0) { throw "Full-tokenizer crypto benchmark exited with code $LASTEXITCODE" }
    Write-Stage 'SUCCESS: cryptographic correctness, matrix, and complete-tokenizer run completed'
}
catch {
    Write-Stage ('ERROR: ' + ($_ | Out-String).TrimEnd())
    throw
}
finally {
    Pop-Location
    $Stopwatch.Stop()
    Write-Stage ('TOTAL ELAPSED: ' + $Stopwatch.Elapsed)
}
