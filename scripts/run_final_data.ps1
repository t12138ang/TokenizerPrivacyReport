[CmdletBinding()]
param(
    [switch]$Resume,
    [string]$EnvironmentPrefix
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CondaExe = 'D:\anaconda\Scripts\conda.exe'
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Config = Join-Path $ProjectRoot 'configs\final_data.json'
$Log = Join-Path $ProjectRoot 'logs\final\data.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Stage([string]$Message) {
    $line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o),$Stopwatch.Elapsed,$Message)
    Write-Host $line
    Add-Content -LiteralPath $Log -Value $line -Encoding utf8
}

New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    Write-Stage '[Final data 1/4] fixed-revision bounded streaming collection'
    $arguments = @('run','--no-capture-output','--prefix',$EnvironmentPrefix,'python','-u','-m','src.data.stream_final_c4','--config',$Config,'--log',$Log)
    if ($Resume) { $arguments += '--resume' }
    & $CondaExe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Final C4 collection exited with code $LASTEXITCODE" }
    Write-Stage '[Final data 2/4] immutable role and shadow manifests'
    & $CondaExe run --no-capture-output --prefix $EnvironmentPrefix python -u -m src.data.build_final_manifests --config $Config --log $Log
    if ($LASTEXITCODE -ne 0) { throw "Final manifest generation exited with code $LASTEXITCODE" }
    Write-Stage '[Final data 3/4] isolation, deduplication, and quality validation'
    & $CondaExe run --no-capture-output --prefix $EnvironmentPrefix python -u -m src.data.validate_final_dataset --config $Config --log $Log
    if ($LASTEXITCODE -ne 0) { throw "Final validation exited with code $LASTEXITCODE" }
    Write-Stage '[Final data 4/4] read-only result gate'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'check_final_data.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Final data check exited with code $LASTEXITCODE" }
    Write-Stage 'SUCCESS: final Development/Main data completed'
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
