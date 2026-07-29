[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Config = Join-Path $ProjectRoot 'configs\final_study.json'
$Log = Join-Path $ProjectRoot 'logs\final\check_attacks.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    & $Python -u -m src.summarize_final_attacks --config $Config 2>&1 | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "Attack result check exited with code $LASTEXITCODE" }
    Write-Host ('Attack check elapsed: ' + $Stopwatch.Elapsed)
}
finally {
    Pop-Location
    $Stopwatch.Stop()
}
