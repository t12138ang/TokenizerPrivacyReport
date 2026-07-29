[CmdletBinding()]
param(
    [string]$EnvironmentPrefix,
    [switch]$FastReport
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Log = Join-Path $ProjectRoot 'logs\final\package_artifacts.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    Write-Host ('Packaging final artifacts: ' + (Get-Date -Format o))
    $Arguments = @('-u', '-m', 'src.package_artifacts', '--output-dir', (Join-Path $ProjectRoot 'artifacts'))
    if ($FastReport) { $Arguments += '--fast-report' }
    & $Python @Arguments 2>&1 | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "Artifact packaging exited with code $LASTEXITCODE" }
    Write-Host ('SUCCESS: artifact packaging elapsed=' + $Stopwatch.Elapsed)
}
finally {
    Pop-Location
    $Stopwatch.Stop()
}
