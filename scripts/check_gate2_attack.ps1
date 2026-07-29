[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SummaryPath = Join-Path $ProjectRoot 'results\gate2\summary.json'
$ResourcePath = Join-Path $ProjectRoot 'results\gate2\resource_profile.csv'
$ExtrapolationPath = Join-Path $ProjectRoot 'results\gate2\resource_extrapolation_96.json'
$LogPath = Join-Path $ProjectRoot 'logs\gate2\check_attack.log'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Gate2AttackCheck {
    param([string]$Message)
    $Line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o), $Stopwatch.Elapsed.ToString(), $Message)
    Write-Output $Line
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding utf8
}

New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
try {
    Write-Gate2AttackCheck '[Gate2 attack check 1/2] strict cardinality and status'
    if (-not (Test-Path -LiteralPath $SummaryPath)) { throw "Missing summary: $SummaryPath" }
    if (-not (Test-Path -LiteralPath $ResourcePath)) { throw "Missing resource profile: $ResourcePath" }
    if (-not (Test-Path -LiteralPath $ExtrapolationPath)) { throw "Missing resource extrapolation: $ExtrapolationPath" }
    $Summary = Get-Content -LiteralPath $SummaryPath -Raw -Encoding utf8 | ConvertFrom-Json
    $Extrapolation = Get-Content -LiteralPath $ExtrapolationPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($Summary.status -ne 'success') { throw 'Summary status is not success' }
    if ($Summary.completed_attack_results -ne $Summary.expected_attack_results) { throw 'Attack result count mismatch' }
    if ($Summary.completed_tokenizer_profiles -ne $Summary.expected_tokenizer_profiles) { throw 'Tokenizer profile count mismatch' }
    if ($Summary.score_direction -ne 'higher_is_more_member') { throw 'Score direction mismatch' }
    if ($Extrapolation.status -ne 'estimate_from_gate2_measurements') { throw 'Resource extrapolation status mismatch' }
    if ($Extrapolation.observed_shadow_count -ne 8 -or $Extrapolation.target_shadow_count -ne 96) { throw 'Resource extrapolation shadow counts mismatch' }
    $Resources = Import-Csv -LiteralPath $ResourcePath
    Write-Gate2AttackCheck '[Gate2 attack check 2/2] summary'
    Write-Gate2AttackCheck ('Status: ' + $Summary.status)
    Write-Gate2AttackCheck ('Attack results: {0}/{1}' -f $Summary.completed_attack_results,$Summary.expected_attack_results)
    Write-Gate2AttackCheck ('Tokenizer profiles: {0}/{1}' -f $Summary.completed_tokenizer_profiles,$Summary.expected_tokenizer_profiles)
    Write-Gate2AttackCheck ('Protocols: ' + ($Summary.protocols -join ', '))
    Write-Gate2AttackCheck ('Seeds: ' + ($Summary.seeds -join ', '))
    Write-Gate2AttackCheck ('Vocab sizes: ' + ($Summary.vocab_sizes -join ', '))
    Write-Gate2AttackCheck ('Methods: ' + (($Summary.methods | ForEach-Object { $_.id }) -join ', '))
    Write-Gate2AttackCheck ('Attacks: ' + ($Summary.attacks -join ', '))
    $Peak = ($Resources | Measure-Object -Property peak_memory_bytes -Maximum).Maximum
    Write-Gate2AttackCheck ('Peak tokenizer memory bytes: ' + $Peak)
    Write-Gate2AttackCheck ('Pipeline elapsed seconds: ' + $Summary.run_state.accumulated_elapsed_seconds)
    Write-Gate2AttackCheck ('Estimated 96-shadow pipeline seconds: ' + $Extrapolation.estimated_for_target_shadow_count.pipeline_seconds)
    Write-Gate2AttackCheck ('Estimated 96-shadow artifact bytes: ' + $Extrapolation.estimated_for_target_shadow_count.tokenizer_artifact_bytes)
    Write-Gate2AttackCheck ('Failures: ' + $Summary.run_state.failures)
    Write-Gate2AttackCheck ('TOTAL ELAPSED: ' + $Stopwatch.Elapsed.ToString())
}
catch {
    $FullError = $_ | Out-String
    Write-Gate2AttackCheck ('ERROR: ' + $FullError.TrimEnd())
    exit 1
}
