[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Config = Join-Path $ProjectRoot 'configs\final_study.json'
$Log = Join-Path $ProjectRoot 'logs\final\fast_report_audit.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    Write-Host ('Fast report audit started: ' + (Get-Date -Format o))
    & $Python -u -m src.reporting.audit_fast_report --config $Config --require-pdf 2>&1 |
        Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "Fast report audit exited with code $LASTEXITCODE" }
    git diff --check 2>&1 | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw 'git diff --check failed' }
    Write-Host ('SUCCESS: fast report audit complete; elapsed=' + $Stopwatch.Elapsed)
}
catch {
    Add-Content -LiteralPath $Log -Value (($_ | Out-String).TrimEnd()) -Encoding utf8
    throw
}
finally {
    Pop-Location
    $Stopwatch.Stop()
}
