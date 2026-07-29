[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CondaExe = 'D:\anaconda\Scripts\conda.exe'
$ConfigPath = Join-Path $ProjectRoot 'configs\gate2_data.json'
$LogPath = Join-Path $ProjectRoot 'logs\gate2\data_pipeline.log'
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Gate2DataStage {
    param([string]$Message)
    $Line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o), $Stopwatch.Elapsed.ToString(), $Message)
    Write-Host $Line
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding utf8
}

New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
Push-Location $ProjectRoot
try {
    Write-Gate2DataStage '[Gate2 data 1/4] preflight'
    if (-not (Test-Path -LiteralPath $CondaExe)) { throw "Conda not found: $CondaExe" }
    if (-not (Test-Path -LiteralPath $EnvironmentPrefix)) { throw "Environment not found: $EnvironmentPrefix" }
    $Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
    $CorpusDir = Join-Path $ProjectRoot $Config.corpus_dir
    $ManifestDir = Join-Path $ProjectRoot $Config.manifest_dir
    $ValidationPath = Join-Path $ProjectRoot $Config.validation_path
    if (Test-Path -LiteralPath $CorpusDir) { throw "Refusing to overwrite corpus: $CorpusDir" }
    if (Test-Path -LiteralPath $ManifestDir) { throw "Refusing to overwrite manifests: $ManifestDir" }
    if (Test-Path -LiteralPath $ValidationPath) { throw "Refusing to overwrite validation: $ValidationPath" }
    $env:PYTHONHASHSEED = [string]$Config.seed
    $env:TOKENIZERS_PARALLELISM = 'false'
    $env:HF_HOME = Join-Path $ProjectRoot $Config.hf_cache_dir
    $env:HF_DATASETS_CACHE = Join-Path $env:HF_HOME 'datasets'

    Write-Gate2DataStage '[Gate2 data 2/4] bounded two-pass C4 stream collection'
    & $CondaExe run --no-capture-output --prefix $EnvironmentPrefix python -u -m src.data.stream_c4_websites --config $ConfigPath --log $LogPath
    if ($LASTEXITCODE -ne 0) { throw "C4 collection exited with code $LASTEXITCODE" }

    Write-Gate2DataStage '[Gate2 data 3/4] immutable protocol manifests'
    & $CondaExe run --no-capture-output --prefix $EnvironmentPrefix python -u -m src.data.build_pilot_manifest --config $ConfigPath --log $LogPath
    if ($LASTEXITCODE -ne 0) { throw "Manifest generation exited with code $LASTEXITCODE" }

    Write-Gate2DataStage '[Gate2 data 4/4] duplicate, quality, and protocol validation'
    & $CondaExe run --no-capture-output --prefix $EnvironmentPrefix python -u -m src.data.validate_pilot_dataset --config $ConfigPath --log $LogPath
    if ($LASTEXITCODE -ne 0) { throw "Data validation exited with code $LASTEXITCODE" }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'check_gate2_data.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Data check exited with code $LASTEXITCODE" }
    Write-Gate2DataStage 'SUCCESS: Gate 2 natural-data pipeline completed'
}
catch {
    $FullError = $_ | Out-String
    Write-Gate2DataStage ('ERROR: ' + $FullError.TrimEnd())
    throw
}
finally {
    Pop-Location
    $Stopwatch.Stop()
    Write-Gate2DataStage ('TOTAL ELAPSED: ' + $Stopwatch.Elapsed.ToString())
}
