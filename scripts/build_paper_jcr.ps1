[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$paperRoot = Join-Path $repoRoot 'paper_jcr'
$buildRoot = Join-Path $paperRoot 'build'
$templateRoot = Join-Path $paperRoot 'template'

if (-not (Test-Path -LiteralPath (Join-Path $paperRoot 'main.tex'))) {
    throw "Missing paper_jcr/main.tex"
}

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$previousTexInputs = $env:TEXINPUTS
$env:TEXINPUTS = "$templateRoot;"
if ($previousTexInputs) {
    $env:TEXINPUTS += $previousTexInputs
}

$buildLog = Join-Path $buildRoot 'build.log'
$warningLog = Join-Path $buildRoot 'warnings.txt'
$bibLog = Join-Path $buildRoot 'bib.log'
$xelatexLog = Join-Path $buildRoot 'xelatex_final.log'
$started = Get-Date

try {
    Push-Location $paperRoot
    for ($pass = 1; $pass -le 3; $pass++) {
        & xelatex.exe `
            -interaction=nonstopmode `
            -halt-on-error `
            -file-line-error `
            -output-directory=build `
            main.tex 2>&1 |
            Tee-Object -FilePath $xelatexLog |
            Out-Null
        if ($LASTEXITCODE -ne 0) {
            @(
                "BUILD_START=$($started.ToString('o'))"
                "FAILED_XELATEX_PASS=$pass"
                Get-Content -LiteralPath $xelatexLog -Encoding UTF8
            ) | Set-Content -LiteralPath $buildLog -Encoding UTF8
            throw "XeLaTeX pass $pass failed with exit code $LASTEXITCODE"
        }
    }

    Copy-Item -LiteralPath (Join-Path $buildRoot 'main.pdf') -Destination (Join-Path $paperRoot 'main.pdf') -Force
    @(
        'The official template archive contains no .bst file.'
        'No external BibTeX style was invoked.'
        'generated/references.tex is a mechanically converted numeric bibliography from the frozen legacy .bbl, whose source remains references.bib.'
    ) | Set-Content -LiteralPath $bibLog -Encoding UTF8

    $patterns = @(
        'Undefined control sequence',
        'LaTeX Error',
        'Citation .* undefined',
        'Reference .* undefined',
        'Overfull',
        'Underfull',
        'Missing character',
        'Font Warning',
        'Emergency stop',
        'Fatal error'
    )
    Select-String -LiteralPath (Join-Path $buildRoot 'main.log') -Pattern $patterns |
        ForEach-Object { $_.Line } |
        Set-Content -LiteralPath $warningLog -Encoding UTF8

    $ended = Get-Date
    @(
        "BUILD_START=$($started.ToString('o'))"
        'FINAL_XELATEX_PASS=3'
        Get-Content -LiteralPath $xelatexLog -Encoding UTF8
        "BUILD_END=$($ended.ToString('o'))"
        "ELAPSED_SECONDS=$([math]::Round(($ended - $started).TotalSeconds,3))"
        "OUTPUT=$(Join-Path $paperRoot 'main.pdf')"
    ) | Set-Content -LiteralPath $buildLog -Encoding UTF8
}
finally {
    Pop-Location -ErrorAction SilentlyContinue
    $env:TEXINPUTS = $previousTexInputs
}
