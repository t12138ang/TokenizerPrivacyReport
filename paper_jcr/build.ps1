[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PaperRoot = $PSScriptRoot
$Started = [System.Diagnostics.Stopwatch]::StartNew()

Push-Location $PaperRoot
try {
    for ($Pass = 1; $Pass -le 3; $Pass++) {
        Write-Host ("[XeLaTeX {0}/3] elapsed={1}" -f $Pass, $Started.Elapsed)
        & xelatex.exe -interaction=nonstopmode -halt-on-error -file-line-error main.tex
        if ($LASTEXITCODE -ne 0) {
            throw "XeLaTeX pass $Pass failed with exit code $LASTEXITCODE"
        }
    }
    Write-Host ("SUCCESS: {0}; total elapsed={1}" -f (Join-Path $PaperRoot 'main.pdf'), $Started.Elapsed)
}
finally {
    Pop-Location
    $Started.Stop()
}
