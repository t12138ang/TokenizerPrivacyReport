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
$StageId = $Stage.ToLowerInvariant()
$Log = Join-Path $ProjectRoot 'logs\final\check_defenses.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    & $Python -u -m src.defenses.check_results --config (Join-Path $ProjectRoot 'configs\final_study.json') --stage $StageId 2>&1 | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "Defense result check exited with code $LASTEXITCODE" }
    Write-Host ('Defense check elapsed: ' + $Stopwatch.Elapsed)
}
finally {
    Pop-Location
    $Stopwatch.Stop()
}
