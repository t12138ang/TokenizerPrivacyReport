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
$StudyConfig = Join-Path $ProjectRoot 'configs\final_study.json'
$Study = Get-Content -LiteralPath $StudyConfig -Raw | ConvertFrom-Json
$DownstreamConfig = Join-Path $ProjectRoot 'configs\downstream.json'
$WrapperLog = Join-Path $ProjectRoot 'logs\final\downstream_wrapper.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
function Write-Stage([string]$Message) {
    $line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o),$Stopwatch.Elapsed,$Message)
    Write-Host $line
    Add-Content -LiteralPath $WrapperLog -Value $line -Encoding utf8
}
New-Item -ItemType Directory -Path (Split-Path -Parent $WrapperLog) -Force | Out-Null
Push-Location $ProjectRoot
try {
    $env:PYTHONHASHSEED = [string]$Study.seeds[0]
    $env:TOKENIZERS_PARALLELISM = 'true'
    $env:RAYON_NUM_THREADS = [string]$Study.tokenizers_threads
    $env:CUBLAS_WORKSPACE_CONFIG = ':4096:8'
    $Stages = if ($Stage -eq 'All') { @('Development','Main') } else { @($Stage) }
    foreach ($CurrentStage in $Stages) {
        $StageId = $CurrentStage.ToLowerInvariant()
        $Plan = Join-Path $ProjectRoot ("results\final\downstream\{0}_plan.json" -f $StageId)
        $State = Join-Path $ProjectRoot ("results\final\downstream\{0}_state.json" -f $StageId)
        $Log = Join-Path $ProjectRoot ("logs\final\downstream_{0}.log" -f $StageId)
        if (-not (Test-Path -LiteralPath $Plan)) {
            Write-Stage ("[Downstream/{0}] freeze immutable training plan" -f $CurrentStage)
            & $Python -u -m src.downstream.build_plan --config $StudyConfig --stage $StageId --output $Plan
            if ($LASTEXITCODE -ne 0) { throw "Downstream plan creation exited with code $LASTEXITCODE" }
        }
        Write-Stage ("[Downstream/{0}] AG News four-class Transformer training" -f $CurrentStage)
        & $Python -u -m src.downstream.run_plan --downstream-config $DownstreamConfig --plan $Plan --state $State --log $Log
        if ($LASTEXITCODE -ne 0) { throw "Downstream $CurrentStage exited with code $LASTEXITCODE" }
        if ($CurrentStage -eq 'Development') {
            $Selection = Join-Path $ProjectRoot 'results\final\defenses\main_selection.json'
            if (-not (Test-Path -LiteralPath $Selection)) {
                Write-Stage '[Downstream/Development] apply frozen Macro-F1 constraint and freeze Main selection'
                & $Python -u -m src.defenses.select_main --config $StudyConfig --output $Selection
                if ($LASTEXITCODE -ne 0) { throw "Main selection exited with code $LASTEXITCODE" }
            }
        }
        Write-Stage ("SUCCESS: Downstream {0} complete" -f $CurrentStage)
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
