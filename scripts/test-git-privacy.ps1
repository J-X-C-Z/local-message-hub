$ErrorActionPreference = 'Stop'
$scanner = Join-Path $PSScriptRoot 'assert-package-privacy.ps1'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-git-privacy-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null

function Invoke-Git([string[]]$Arguments) {
    & git -C $root @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Synthetic Git fixture command failed: $($Arguments[0])" }
}

function Assert-ScannerRejects([string]$RelativePath, [string]$Content = 'synthetic fixture') {
    $path = Join-Path $root $RelativePath
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    [System.IO.File]::WriteAllText($path, $Content)
    $rejected = $false
    try { & $scanner -Root $root -GitVisible | Out-Null }
    catch { $rejected = $true }
    if (-not $rejected) { throw "Git privacy scanner accepted a synthetic private fixture: $RelativePath" }
    Remove-Item -LiteralPath $path -Force
}

try {
    Invoke-Git @('init', '--quiet')
    [System.IO.File]::WriteAllText((Join-Path $root 'public.ts'), 'export const ready = true;')
    [System.IO.File]::WriteAllText((Join-Path $root '.gitignore'), "ignored-private.txt`n")
    Invoke-Git @('add', '--', 'public.ts', '.gitignore')
    [System.IO.File]::WriteAllText((Join-Path $root 'ignored-private.txt'), ('ghp_' + 'abcdefghijklmnopqrstuvwxyz0123456789'))
    & $scanner -Root $root -GitVisible | Out-Null

    [System.IO.File]::WriteAllText((Join-Path $root 'public.ts'), ('const changed = "' + 'ghp_' + 'abcdefghijklmnopqrstuvwxyz0123456789";'))
    $modifiedRejected = $false
    try { & $scanner -Root $root -GitVisible | Out-Null }
    catch { $modifiedRejected = $true }
    if (-not $modifiedRejected) { throw 'Git privacy scanner missed a secret added to a modified tracked file.' }
    [System.IO.File]::WriteAllText((Join-Path $root 'public.ts'), 'export const ready = true;')

    Assert-ScannerRejects 'wechat.json'
    Assert-ScannerRejects 'qq-local.json'
    Assert-ScannerRejects '.env'
    Assert-ScannerRejects 'credentials.json'
    Assert-ScannerRejects 'private.key'
    Assert-ScannerRejects 'credential.ts' ('const key = "' + 'ghp_' + 'abcdefghijklmnopqrstuvwxyz0123456789";')
    Assert-ScannerRejects 'cloud.ts' ('const accessKey = "' + 'AKIA' + '1234567890ABCDEF";')
    Assert-ScannerRejects 'slack.ts' ('const botToken = "' + 'xoxb-' + '1234567890-abcdefghijklmnopqrstuvwxyz";')
    Assert-ScannerRejects 'private.pem' (("-----BEGIN RSA " + 'PRIVATE KEY-----') + "`nsynthetic`n" + ("-----END RSA " + 'PRIVATE KEY-----'))
    $profileRoot = [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\', '/').Replace('\', '/')
    Assert-ScannerRejects 'profile-path.md' "local path: $profileRoot/private-data"

    Write-Output 'PASS: Git privacy scan covers staged, modified, and untracked files; ignores excluded local files and rejects synthetic credentials, account configs, and profile paths.'
} finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
