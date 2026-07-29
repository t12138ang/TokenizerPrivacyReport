[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CondaExe = 'D:\anaconda\Scripts\conda.exe'
$ConfigPath = Join-Path $ProjectRoot 'configs\gate2_attack.json'
$LogPath = Join-Path $ProjectRoot 'logs\gate2\attack_pipeline.log'
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Gate2AttackStage {
    param([string]$Message)
    $Line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o), $Stopwatch.Elapsed.ToString(), $Message)
    Write-Host $Line
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding utf8
}

New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
Push-Location $ProjectRoot
try {
    Write-Gate2AttackStage '[Gate2 attack 1/5] preflight and validated-data gate'
    if (-not (Test-Path -LiteralPath $CondaExe)) { throw "Conda not found: $CondaExe" }
    if (-not (Test-Path -LiteralPath $EnvironmentPrefix)) { throw "Environment not found: $EnvironmentPrefix" }
    $ValidationPath = Join-Path $ProjectRoot 'data\gate2\validation.json'
    if (-not (Test-Path -LiteralPath $ValidationPath)) { throw "Missing Gate 2 validation: $ValidationPath" }
    $Validation = Get-Content -LiteralPath $ValidationPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($Validation.status -ne 'success') { throw 'Gate 2 natural-data validation did not pass' }
    $Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
    $SummaryPath = Join-Path $ProjectRoot 'results\gate2\summary.json'
    if (Test-Path -LiteralPath $SummaryPath) { throw "Refusing to overwrite completed summary: $SummaryPath" }
    $env:PYTHONHASHSEED = [string]$Config.seeds[0]
    $env:TOKENIZERS_PARALLELISM = 'true'
    $env:RAYON_NUM_THREADS = [string]$Config.tokenizers_threads

    Write-Gate2AttackStage '[Gate2 attack 2/5] resumable target/shadow tokenizer and attack pipeline'
    & $CondaExe run --no-capture-output --prefix $EnvironmentPrefix python -u -m src.gate2_attack_pipeline --config $ConfigPath --log $LogPath
    if ($LASTEXITCODE -ne 0) { throw "Attack pipeline exited with code $LASTEXITCODE" }

    Write-Gate2AttackStage '[Gate2 attack 3/5] strict result and resource summaries'
    & $CondaExe run --no-capture-output --prefix $EnvironmentPrefix python -u -m src.summarize_gate2 --config $ConfigPath --log $LogPath
    if ($LASTEXITCODE -ne 0) { throw "Summary generation exited with code $LASTEXITCODE" }

    Write-Gate2AttackStage '[Gate2 attack 4/5] reports generated only from machine results'
    & $CondaExe run --no-capture-output --prefix $EnvironmentPrefix python -u -m src.generate_gate2_reports --config $ConfigPath
    if ($LASTEXITCODE -ne 0) { throw "Report generation exited with code $LASTEXITCODE" }

    Write-Gate2AttackStage '[Gate2 attack 5/5] result gate'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'check_gate2_attack.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Attack result check exited with code $LASTEXITCODE" }
    Write-Gate2AttackStage 'SUCCESS: Gate 2 bounded attack pipeline completed'
}
catch {
    $FullError = $_ | Out-String
    Write-Gate2AttackStage ('ERROR: ' + $FullError.TrimEnd())
    throw
}
finally {
    Pop-Location
    $Stopwatch.Stop()
    Write-Gate2AttackStage ('TOTAL ELAPSED: ' + $Stopwatch.Elapsed.ToString())
}
