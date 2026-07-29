[CmdletBinding()]
param([string]$EnvironmentPrefix)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Log = Join-Path $ProjectRoot 'logs\final\run_everything.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
function Run([string]$Name, [scriptblock]$Command) {
    $line = ('{0} | elapsed={1} | stage={2}' -f (Get-Date -Format o),$Stopwatch.Elapsed,$Name)
    Write-Host $line
    Add-Content -LiteralPath $Log -Value $line -Encoding utf8
    & $Command
}
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
Push-Location $ProjectRoot
try {
    Run 'final-data' { & '.\scripts\run_final_data.ps1' -EnvironmentPrefix $EnvironmentPrefix }
    Run 'check-final-data' { & '.\scripts\check_final_data.ps1' -EnvironmentPrefix $EnvironmentPrefix }
    Run 'baseline-attacks' { & '.\scripts\run_attacks.ps1' -EnvironmentPrefix $EnvironmentPrefix }
    Run 'check-baseline-attacks' { & '.\scripts\check_attacks.ps1' -EnvironmentPrefix $EnvironmentPrefix }
    Run 'development-defense-selection-main-defense' { & '.\scripts\run_defenses.ps1' -Stage All -EnvironmentPrefix $EnvironmentPrefix }
    Run 'main-downstream' { & '.\scripts\run_downstream.ps1' -Stage Main -EnvironmentPrefix $EnvironmentPrefix }
    Run 'formal-crypto-benchmark' { & '.\scripts\run_crypto_bench.ps1' -EnvironmentPrefix $EnvironmentPrefix }
    Run 'reporting' { & '.\scripts\generate_all_figures.ps1' -EnvironmentPrefix $EnvironmentPrefix }
    Run 'paper' { & '.\scripts\build_paper.ps1' }
    Run 'final-audit' { & '.\scripts\check_everything.ps1' -EnvironmentPrefix $EnvironmentPrefix }
    Run 'package-artifacts' { & '.\scripts\package_artifacts.ps1' -EnvironmentPrefix $EnvironmentPrefix }
    Write-Host ('SUCCESS: complete final study; total elapsed=' + $Stopwatch.Elapsed)
}
catch {
    Add-Content -LiteralPath $Log -Value (($_ | Out-String).TrimEnd()) -Encoding utf8
    throw
}
finally {
    Pop-Location
    $Stopwatch.Stop()
}
