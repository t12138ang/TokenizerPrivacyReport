[CmdletBinding()]
param(
    [ValidateSet('Development','Main','All')][string]$Stage = 'Development',
    [string]$EnvironmentPrefix
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Config = Join-Path $ProjectRoot 'configs\final_study.json'
$Study = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$Plan = Join-Path $ProjectRoot 'results\final\defenses\development_search_plan.json'
$Log = Join-Path $ProjectRoot 'logs\final\defenses.log'
$WrapperLog = Join-Path $ProjectRoot 'logs\final\defenses_wrapper.log'
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
    if ($Stage -in @('Development','All')) {
        Write-Stage '[Defenses/Development] frozen private-BPE screening, attacks, and utility'
        & $Python -u -m src.defense_pipeline --config $Config --plan $Plan --log $Log
        if ($LASTEXITCODE -ne 0) { throw "Development defense pipeline exited with code $LASTEXITCODE" }
        Write-Stage 'SUCCESS: Development shortlist created without reading Main results'
    }
    if ($Stage -eq 'All') {
        Write-Stage '[Defenses/Selection] run Development AG News constraint before Main'
        & (Join-Path $PSScriptRoot 'run_downstream.ps1') -Stage Development -EnvironmentPrefix $EnvironmentPrefix
        if ($LASTEXITCODE -ne 0) { throw "Development downstream selection exited with code $LASTEXITCODE" }
    }
    if ($Stage -in @('Main','All')) {
        $Selection = Join-Path $ProjectRoot 'results\final\defenses\main_selection.json'
        $MainLog = Join-Path $ProjectRoot 'logs\final\defenses_main.log'
        Write-Stage '[Defenses/Main] frozen selected configurations, five attacks, and utility'
        & $Python -u -m src.main_defense_pipeline --config $Config --selection $Selection --log $MainLog
        if ($LASTEXITCODE -ne 0) { throw "Main defense pipeline exited with code $LASTEXITCODE" }
        Write-Stage 'SUCCESS: Main defenses completed'
    }
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
