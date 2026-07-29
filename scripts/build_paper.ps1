[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PaperRoot = Join-Path $ProjectRoot 'paper'
$BuildRoot = Join-Path $PaperRoot 'build'
$ExpectedBuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'paper\build'))
if ([System.IO.Path]::GetFullPath($BuildRoot) -ne $ExpectedBuildRoot) {
    throw 'Resolved paper build directory failed safety validation'
}
$Log = Join-Path $ProjectRoot 'logs\paper_build.log'
$ArtifactDir = Join-Path $ProjectRoot 'artifacts'
$ArtifactPdf = Join-Path $ArtifactDir 'Tokenizer_Privacy_Course_Report_Draft.pdf'
$PaperPdf = Join-Path $PaperRoot 'main.pdf'
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
function Write-Stage([string]$Message) {
    $line = ('{0} | elapsed={1} | {2}' -f (Get-Date -Format o),$Stopwatch.Elapsed,$Message)
    Write-Host $line
    Add-Content -LiteralPath $Log -Value $line -Encoding utf8
}
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null
if (Test-Path -LiteralPath $ArtifactPdf) {
    $Bytes = [System.IO.File]::ReadAllBytes($ArtifactPdf)
    $PriorLog = if (Test-Path -LiteralPath $Log) { Get-Content -LiteralPath $Log -Raw } else { '' }
    if ($Bytes.Length -lt 10000 -or [System.Text.Encoding]::ASCII.GetString($Bytes, 0, 4) -ne '%PDF') {
        throw "existing paper artifact is not a valid PDF: $ArtifactPdf"
    }
    if ($PriorLog -notmatch 'SUCCESS: two consecutive clean builds') {
        throw 'existing paper artifact has no two-clean-build success record'
    }
    if (-not (Test-Path -LiteralPath $PaperPdf)) {
        throw 'artifact PDF exists but paper\main.pdf is missing'
    }
    Write-Stage ('SUCCESS: verified existing two-clean-build paper artifacts; no overwrite: ' + $PaperPdf)
    $Stopwatch.Stop()
    return
}
if (-not (Test-Path -LiteralPath (Join-Path $PaperRoot 'generated\results_macros.tex'))) {
    throw 'Generated experimental macros are missing; run scripts\generate_all_figures.ps1 first'
}
if (Test-Path -LiteralPath $PaperPdf) {
    throw "refusing to overwrite existing paper PDF: $PaperPdf"
}
Push-Location $PaperRoot
try {
    $UseLatexmk = $null -ne (Get-Command latexmk -ErrorAction SilentlyContinue) -and $null -ne (Get-Command perl -ErrorAction SilentlyContinue)
    $FinalPassRoot = $BuildRoot
    foreach ($Pass in 1..2) {
        $SavedErrorActionPreference = $ErrorActionPreference
        if ($UseLatexmk) {
            Write-Stage ("[Paper] clean build pass $Pass/2 with latexmk")
            $ErrorActionPreference = 'Continue'
            & latexmk -C -outdir=build main.tex 2>&1 | Tee-Object -FilePath $Log -Append
            $CleanExitCode = $LASTEXITCODE
            $ErrorActionPreference = $SavedErrorActionPreference
            if ($CleanExitCode -ne 0) { throw "latexmk clean failed on pass $Pass with code $CleanExitCode" }
            Write-Stage ("[Paper] XeLaTeX/BibTeX build pass $Pass/2")
            $ErrorActionPreference = 'Continue'
            & latexmk -xelatex -bibtex -outdir=build main.tex 2>&1 | Tee-Object -FilePath $Log -Append
            $BuildExitCode = $LASTEXITCODE
            $ErrorActionPreference = $SavedErrorActionPreference
            if ($BuildExitCode -ne 0) { throw "paper build failed on clean pass $Pass with code $BuildExitCode" }
            $FinalPassRoot = $BuildRoot
        }
        else {
            $PassRoot = Join-Path $BuildRoot ("clean_pass_{0}" -f $Pass)
            if (Test-Path -LiteralPath $PassRoot) {
                throw "manual clean-build directory already exists: $PassRoot"
            }
            New-Item -ItemType Directory -Path $PassRoot | Out-Null
            Write-Stage ("[Paper] manual clean XeLaTeX/BibTeX build pass $Pass/2 (latexmk Perl unavailable)")
            $ErrorActionPreference = 'Continue'
            $OutputDirectoryArgument = ("-output-directory={0}" -f $PassRoot)
            & xelatex -interaction=nonstopmode -halt-on-error -file-line-error $OutputDirectoryArgument main.tex 2>&1 | Tee-Object -FilePath $Log -Append
            $XeOneExitCode = $LASTEXITCODE
            if ($XeOneExitCode -eq 0) {
                $BibTarget = Join-Path $PassRoot 'main'
                & bibtex $BibTarget 2>&1 | Tee-Object -FilePath $Log -Append
                $BibExitCode = $LASTEXITCODE
            }
            else { $BibExitCode = -1 }
            if ($XeOneExitCode -eq 0 -and $BibExitCode -eq 0) {
                & xelatex -interaction=nonstopmode -halt-on-error -file-line-error $OutputDirectoryArgument main.tex 2>&1 | Tee-Object -FilePath $Log -Append
                $XeTwoExitCode = $LASTEXITCODE
            }
            else { $XeTwoExitCode = -1 }
            if ($XeTwoExitCode -eq 0) {
                & xelatex -interaction=nonstopmode -halt-on-error -file-line-error $OutputDirectoryArgument main.tex 2>&1 | Tee-Object -FilePath $Log -Append
                $XeThreeExitCode = $LASTEXITCODE
            }
            else { $XeThreeExitCode = -1 }
            $ErrorActionPreference = $SavedErrorActionPreference
            if ($XeOneExitCode -ne 0 -or $BibExitCode -ne 0 -or $XeTwoExitCode -ne 0 -or $XeThreeExitCode -ne 0) {
                throw "manual paper build failed on pass $Pass (xelatex1=$XeOneExitCode bibtex=$BibExitCode xelatex2=$XeTwoExitCode xelatex3=$XeThreeExitCode)"
            }
            $FinalPassRoot = $PassRoot
        }
        if (-not (Test-Path -LiteralPath (Join-Path $FinalPassRoot 'main.pdf'))) {
            throw "paper PDF missing after pass $Pass"
        }
    }
    if (-not $UseLatexmk) {
        $FinalBuildPdf = Join-Path $BuildRoot 'main.pdf'
        if (Test-Path -LiteralPath $FinalBuildPdf) { throw "refusing to overwrite existing final build PDF: $FinalBuildPdf" }
        Copy-Item -LiteralPath (Join-Path $FinalPassRoot 'main.pdf') -Destination $FinalBuildPdf
    }
    $TexLog = Join-Path $FinalPassRoot 'main.log'
    $FatalPatterns = 'Undefined control sequence','Citation .* undefined','Reference .* undefined','There were undefined references','I couldn''t open database file','File .* not found'
    foreach ($Pattern in $FatalPatterns) {
        $Matches = Select-String -LiteralPath $TexLog -Pattern $Pattern
        if ($Matches) { throw "paper audit found fatal pattern: $Pattern" }
    }
    if (Test-Path -LiteralPath $ArtifactPdf) {
        throw "refusing to overwrite existing paper artifact: $ArtifactPdf"
    }
    $PaperPartial = $PaperPdf + '.partial'
    if (Test-Path -LiteralPath $PaperPartial) {
        throw "partial paper PDF requires audit: $PaperPartial"
    }
    Copy-Item -LiteralPath (Join-Path $BuildRoot 'main.pdf') -Destination $PaperPartial
    [System.IO.File]::Move($PaperPartial, $PaperPdf)
    $ArtifactPartial = $ArtifactPdf + '.partial'
    if (Test-Path -LiteralPath $ArtifactPartial) {
        throw "partial paper artifact requires audit: $ArtifactPartial"
    }
    Copy-Item -LiteralPath $PaperPdf -Destination $ArtifactPartial
    [System.IO.File]::Move($ArtifactPartial, $ArtifactPdf)
    Write-Stage ('SUCCESS: two consecutive clean builds; PDF=' + $PaperPdf + '; artifact=' + $ArtifactPdf)
}
catch {
    Write-Stage ('ERROR: ' + ($_ | Out-String).TrimEnd())
    throw
}
finally {
    Pop-Location
    $Stopwatch.Stop()
    Write-Stage ('TOTAL ELAPSED: ' + $Stopwatch.Elapsed)
}
