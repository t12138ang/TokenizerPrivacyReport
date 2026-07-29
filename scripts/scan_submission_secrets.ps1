[CmdletBinding()]
param(
    [Parameter()]
    [string]$Root = '.'
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$textExtensions = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
@(
    '.bib', '.cfg', '.csv', '.json', '.md', '.ps1', '.py', '.sh', '.sty',
    '.tex', '.txt', '.yml', '.yaml', '.toml', '.ini', '.gitignore', '.gitattributes'
) | ForEach-Object { [void]$textExtensions.Add($_) }

$contentPatterns = [ordered]@{
    private_key       = [regex]'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'
    github_token      = [regex]'(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255})'
    openai_key        = [regex]'\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b'
    aws_access_key    = [regex]'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'
    google_api_key    = [regex]'\bAIza[0-9A-Za-z_-]{35}\b'
    slack_token       = [regex]'\bxox[baprs]-[0-9A-Za-z-]{20,}\b'
    bearer_token      = [regex]'(?i)\bAuthorization\s*[:=]\s*["'']?Bearer\s+[A-Za-z0-9._~+/-]{20,}'
    assigned_secret   = [regex]'(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd)\b\s*[:=]\s*["''][^"'']{8,}["'']'
}

$sensitiveNamePattern = [regex]'(?i)(?:^|[/\\])(?:\.env(?:\..+)?|id_(?:rsa|dsa|ecdsa|ed25519)|credentials(?:\.[^/\\]+)?|secrets?(?:\.[^/\\]+)?|.*\.pem|.*\.p12|.*\.pfx)$'
$findings = [System.Collections.Generic.List[object]]::new()
$files = Get-ChildItem -LiteralPath $resolvedRoot -Recurse -File -Force |
    Where-Object { $_.FullName -notmatch '[\\/]\.git[\\/]' }

foreach ($file in $files) {
    $relative = $file.FullName.Substring($resolvedRoot.Length).TrimStart([char[]]@([char]92, [char]47))
    if ($sensitiveNamePattern.IsMatch($relative)) {
        $findings.Add([pscustomobject]@{ File = $relative; Line = 0; Pattern = 'sensitive_filename' })
    }

    $extension = $file.Extension
    $isKnownText = $textExtensions.Contains($extension) -or $file.Name -in @('.gitignore', '.gitattributes')
    if (-not $isKnownText) {
        continue
    }

    $lineNumber = 0
    foreach ($line in Get-Content -LiteralPath $file.FullName -Encoding UTF8 -ErrorAction Stop) {
        $lineNumber++
        foreach ($entry in $contentPatterns.GetEnumerator()) {
            if ($entry.Value.IsMatch($line)) {
                $findings.Add([pscustomobject]@{
                    File = $relative
                    Line = $lineNumber
                    Pattern = $entry.Key
                })
            }
        }
    }
}

Write-Host "SECRET_SCAN_ROOT=$resolvedRoot"
Write-Host "SECRET_SCAN_FILES=$($files.Count)"
Write-Host "SECRET_SCAN_FINDINGS=$($findings.Count)"
if ($findings.Count -gt 0) {
    $findings | Sort-Object File, Line, Pattern | Format-Table -AutoSize
    exit 1
}

Write-Host 'SECRET_SCAN_STATUS=success'
