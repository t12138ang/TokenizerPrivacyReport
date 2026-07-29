[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Log = Join-Path $ProjectRoot 'logs\final\attacks.log'
$WrapperLog = Join-Path $ProjectRoot 'logs\final\attacks_wrapper.log'
$Config = Join-Path $ProjectRoot 'configs\final_study.json'
$Study = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
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
    $env:TOKENIZERS_PARALLELISM = 'true'
    $env:RAYON_NUM_THREADS = [string]$Study.tokenizers_threads
    Write-Stage '[Final attacks] Development/Main tokenizer training and five attacks; resumable checkpoints enabled'
    & $Python -u -m src.final_attack_pipeline --config $Config --log $Log
    if ($LASTEXITCODE -ne 0) { throw "Final attack pipeline exited with code $LASTEXITCODE" }
    Write-Stage 'SUCCESS: attack pipeline completed; run scripts\check_attacks.ps1 to summarize'
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
