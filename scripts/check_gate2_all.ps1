[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
try {
    Write-Host ('{0} | elapsed={1} | [Gate2 all 1/2] data gate' -f (Get-Date -Format o),$Stopwatch.Elapsed)
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'check_gate2_data.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Data check exited with code $LASTEXITCODE" }
    Write-Host ('{0} | elapsed={1} | [Gate2 all 2/2] attack gate' -f (Get-Date -Format o),$Stopwatch.Elapsed)
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'check_gate2_attack.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Attack check exited with code $LASTEXITCODE" }
    Write-Host ('{0} | elapsed={1} | SUCCESS: all Gate 2 gates passed' -f (Get-Date -Format o),$Stopwatch.Elapsed)
}
catch {
    Write-Error ($_ | Out-String)
    exit 1
}
finally {
    $Stopwatch.Stop()
    Write-Host ('{0} | total_elapsed={1}' -f (Get-Date -Format o),$Stopwatch.Elapsed)
}
