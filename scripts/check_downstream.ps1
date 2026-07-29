[CmdletBinding()]
param(
    [ValidateSet('Development','Main','All')][string]$Stage = 'All',
    [string]$EnvironmentPrefix
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Stages = if ($Stage -eq 'All') { @('development','main') } else { @($Stage.ToLowerInvariant()) }
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
foreach ($CurrentStage in $Stages) {
    $Plan = Join-Path $ProjectRoot ("results\final\downstream\{0}_plan.json" -f $CurrentStage)
    $State = Join-Path $ProjectRoot ("results\final\downstream\{0}_state.json" -f $CurrentStage)
    if (-not (Test-Path -LiteralPath $Plan) -or -not (Test-Path -LiteralPath $State)) {
        throw "Missing downstream plan/state for $CurrentStage"
    }
    $PlanJson = Get-Content -LiteralPath $Plan -Raw | ConvertFrom-Json
    $StateJson = Get-Content -LiteralPath $State -Raw | ConvertFrom-Json
    $Successes = 0
    $Rows = @()
    foreach ($Task in $PlanJson.tasks) {
        $ResultPath = Join-Path $ProjectRoot (Join-Path $Task.output_dir 'result.json')
        if (Test-Path -LiteralPath $ResultPath) {
            $Result = Get-Content -LiteralPath $ResultPath -Raw | ConvertFrom-Json
            if ($Result.status -eq 'success') { $Successes += 1 }
            $Rows += [pscustomobject]@{Stage=$CurrentStage;Method=$Task.method_id;Seed=$Task.seed;Accuracy=$Result.test.accuracy;MacroF1=$Result.test.macro_f1;Device=$Result.device;ElapsedSeconds=$Result.training_elapsed_seconds}
        }
    }
    Write-Host ("stage={0} status={1} successes={2}/{3} failures={4}" -f $CurrentStage,$StateJson.status,$Successes,$PlanJson.task_count,$StateJson.failures)
    $Rows | Format-Table -AutoSize
    if ($StateJson.status -ne 'success' -or $Successes -ne [int]$PlanJson.task_count) {
        throw "Downstream $CurrentStage is incomplete"
    }
}
Write-Host ('Downstream check elapsed: ' + $Stopwatch.Elapsed)
