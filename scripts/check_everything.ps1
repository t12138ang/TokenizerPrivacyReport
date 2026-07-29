[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Log = Join-Path $ProjectRoot 'logs\final\check_everything.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    Write-Host ('Final audit started: ' + (Get-Date -Format o))
    & $Python -u -m src.audit_final --config (Join-Path $ProjectRoot 'configs\final_study.json') 2>&1 | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "Final audit exited with code $LASTEXITCODE" }
    git diff --check 2>&1 | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw 'git diff --check failed' }
    git status --short 2>&1 | Tee-Object -FilePath $Log -Append
    Write-Host ('SUCCESS: final audit complete; elapsed=' + $Stopwatch.Elapsed)
}
finally {
    Pop-Location
    $Stopwatch.Stop()
}
