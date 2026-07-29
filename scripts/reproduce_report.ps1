[CmdletBinding()]
param(
    [switch]$Attacks,
    [switch]$Defense,
    [switch]$Downstream,
    [switch]$Crypto,
    [switch]$FiguresOnly,
    [switch]$PaperOnly,
    [string]$EnvironmentPrefix
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvironmentPrefix)) {
    $EnvironmentPrefix = Join-Path $ProjectRoot '.conda\envs\tokenizer-privacy-report'
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$Selected = @($Attacks, $Defense, $Downstream, $Crypto, $FiguresOnly, $PaperOnly) | Where-Object { $_ }

if ($Selected.Count -eq 0) {
    Write-Host 'No stage selected. Choose one or more of:' -ForegroundColor Yellow
    Write-Host '  -Attacks -Defense -Downstream -Crypto -FiguresOnly -PaperOnly'
    Write-Host 'High-cost experiments are never started by default.'
    exit 2
}

$Stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$Log = Join-Path $ProjectRoot ("logs\reproduce_report_{0}.log" -f $Stamp)
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
$Total = [System.Diagnostics.Stopwatch]::StartNew()
$StageIndex = 0
$StageCount = $Selected.Count

function Write-RunLine([string]$Message) {
    $Line = '{0} | elapsed={1} | {2}' -f (Get-Date -Format o), $Total.Elapsed, $Message
    Write-Host $Line
    Add-Content -LiteralPath $Log -Value $Line -Encoding utf8
}

function Invoke-ReportStage(
    [string]$Name,
    [scriptblock]$Command,
    [string]$StageLog,
    [string]$ResultCommand
) {
    $script:StageIndex += 1
    Write-Progress -Activity 'TokenizerPrivacyReport reproduction' -Status $Name -PercentComplete ([int](100 * ($script:StageIndex - 1) / $StageCount))
    Write-RunLine ("START [{0}/{1}] {2}" -f $script:StageIndex, $StageCount, $Name)
    & $Command
    if (-not $?) { throw "Stage failed: $Name" }
    Write-RunLine ("DONE  [{0}/{1}] {2}; stage_log={3}; result_check={4}" -f $script:StageIndex, $StageCount, $Name, $StageLog, $ResultCommand)
}

Push-Location $ProjectRoot
try {
    if ($Attacks) {
        Invoke-ReportStage 'five tokenizer membership attacks' {
            & (Join-Path $PSScriptRoot 'run_attacks.ps1') -EnvironmentPrefix $EnvironmentPrefix
        } 'logs/final/attacks.log' 'scripts/check_attacks.ps1'
    }
    if ($Defense) {
        Invoke-ReportStage 'defense selection and independent evaluation' {
            & (Join-Path $PSScriptRoot 'run_defenses.ps1') -Stage All -EnvironmentPrefix $EnvironmentPrefix
        } 'logs/final/defenses_wrapper.log' 'scripts/check_defenses.ps1 -Stage All'
    }
    if ($Downstream) {
        Invoke-ReportStage 'AG News independent evaluation' {
            & (Join-Path $PSScriptRoot 'run_downstream.ps1') -Stage Main -EnvironmentPrefix $EnvironmentPrefix
        } 'logs/final/downstream_wrapper.log' 'scripts/check_downstream.ps1 -Stage Main'
    }
    if ($Crypto) {
        Invoke-ReportStage '2048-bit Paillier correctness and cost benchmark' {
            & (Join-Path $PSScriptRoot 'run_crypto_bench.ps1') -EnvironmentPrefix $EnvironmentPrefix
        } 'logs/final/crypto_wrapper.log' 'scripts/check_crypto_bench.ps1'
    }
    if ($FiguresOnly) {
        Invoke-ReportStage 'figures and LaTeX tables from recorded results' {
            & $Python -u -m src.reporting.generate_jcr_assets
            if ($LASTEXITCODE -ne 0) { throw "JCR asset generation exited with code $LASTEXITCODE" }
        } $Log 'Get-ChildItem paper_jcr/figures,paper_jcr/tables,paper_jcr/generated'
    }
    if ($PaperOnly) {
        Invoke-ReportStage 'JCR-template paper build' {
            & (Join-Path $ProjectRoot 'paper_jcr\build.ps1')
        } 'paper_jcr/main.log' 'Get-Item paper_jcr/main.pdf'
    }
    Write-Progress -Activity 'TokenizerPrivacyReport reproduction' -Completed
    Write-RunLine ("SUCCESS selected stages completed; dispatch_log={0}" -f $Log)
}
catch {
    Write-RunLine ('ERROR ' + ($_ | Out-String).TrimEnd())
    throw
}
finally {
    Pop-Location
    $Total.Stop()
    Write-RunLine ('TOTAL ELAPSED ' + $Total.Elapsed)
}
