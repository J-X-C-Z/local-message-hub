$ErrorActionPreference = 'Stop'
$scanner = Join-Path $PSScriptRoot 'assert-package-privacy.ps1'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-privacy-test-' + [guid]::NewGuid().ToString('N'))
$root = [System.IO.Path]::GetFullPath($root)
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
if ([System.IO.Path]::GetDirectoryName($root) -ne $tempRoot -or
    [System.IO.Path]::GetFileName($root) -notmatch '^lmh-privacy-test-[a-f0-9]{32}$') {
    throw 'Refusing to create or clean an unexpected privacy test directory.'
}
New-Item -ItemType Directory -Path $root | Out-Null

function Assert-ScannerRejects([string]$FileName) {
    $path = Join-Path $root $FileName
    [System.IO.File]::WriteAllText($path, 'synthetic fixture')
    $rejected = $false
    try { & $scanner -Root $root | Out-Null }
    catch { $rejected = $true }
    Remove-Item -LiteralPath $path -Force
    if (-not $rejected) { throw "Privacy scanner accepted forbidden fixture: $FileName" }
}

function Assert-ScannerRejectsProfilePaths {
    $roots = @($env:USERPROFILE, $env:LOCALAPPDATA) | Where-Object { $_ }
    $checked = 0
    foreach ($profileRoot in $roots) {
        $normalized = [System.IO.Path]::GetFullPath($profileRoot).TrimEnd('\', '/').Replace('\', '/')
        $fixture = Join-Path $root ("profile-path-{0}.txt" -f $checked)
        [System.IO.File]::WriteAllText($fixture, "synthetic local path: $normalized/private-data")
        $rejected = $false
        try { & $scanner -Root $root | Out-Null }
        catch { $rejected = $true }
        Remove-Item -LiteralPath $fixture -Force
        if (-not $rejected) { throw 'Privacy scanner accepted a synthetic current-user path fixture.' }
        $checked++
    }
    if ($checked -eq 0) { throw 'No current-user path environment variable was available for the privacy test.' }
}

function Assert-ScannerRejectsRedirectedAppData {
    $name = 'APPDATA'
    $oldValue = [System.Environment]::GetEnvironmentVariable($name, 'Process')
    $redirectedRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-roaming-data-' + [guid]::NewGuid().ToString('N'))
    try {
        [System.Environment]::SetEnvironmentVariable($name, $redirectedRoot, 'Process')
        $normalized = [System.IO.Path]::GetFullPath($redirectedRoot).TrimEnd('\', '/').Replace('\', '/')
        [System.IO.File]::WriteAllText((Join-Path $root 'redirected-appdata.txt'), "synthetic local path: $normalized/private-config")
        $rejected = $false
        try { & $scanner -Root $root | Out-Null }
        catch { $rejected = $true }
        if (-not $rejected) { throw 'Privacy scanner accepted a redirected APPDATA path outside USERPROFILE.' }
    } finally {
        Remove-Item -LiteralPath (Join-Path $root 'redirected-appdata.txt') -Force -ErrorAction SilentlyContinue
        [System.Environment]::SetEnvironmentVariable($name, $oldValue, 'Process')
    }
}

function Assert-ScannerReadsTabularExports {
    if (-not $env:USERPROFILE) { throw 'USERPROFILE is required for the tabular privacy fixture.' }
    $normalized = [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\', '/').Replace('\', '/')
    foreach ($extension in @('.csv', '.tsv')) {
        $fixture = Join-Path $root ("private-export{0}" -f $extension)
        [System.IO.File]::WriteAllText($fixture, "exported_value`n$normalized/private-chat.db")
        $rejected = $false
        try { & $scanner -Root $root | Out-Null }
        catch { $rejected = $true }
        Remove-Item -LiteralPath $fixture -Force
        if (-not $rejected) { throw "Privacy scanner skipped private profile data in $extension." }
    }
}

function Assert-SourcePackagerExcludesTabularExports {
    $packager = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'package-windows-source.ps1'))
    $match = [regex]::Match($packager, '(?s)\$forbiddenExtensions\s*=\s*@\((.*?)\)')
    if (-not $match.Success) { throw 'Source packager forbidden-extension list was not found.' }
    foreach ($extension in @('.csv', '.tsv')) {
        if (-not $match.Groups[1].Value.Contains("'$extension'")) {
            throw "Source packager must exclude tabular data files: $extension"
        }
    }
}

function Assert-ScannerAuditedPlaceholder {
    $asset = '.runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64/static/assets/index-Cgjz17KL.js'
    $placeholder = 'placeholder:`-----BEGIN ' + 'PRIVATE KEY-----' + "`n...`n" + '-----END ' + 'PRIVATE KEY-----`'
    $key = '-----BEGIN ' + 'PRIVATE KEY-----' + "`nsynthetic-key-material`n" + '-----END ' + 'PRIVATE KEY-----'
    foreach ($prefix in @('', '_internal/', 'mcp/_internal/')) {
        $path = Join-Path $root ($prefix + $asset)
        New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
        try {
            [System.IO.File]::WriteAllText($path, $placeholder)
            & $scanner -Root $root | Out-Null
            foreach ($content in @(($placeholder + "`n" + $key), ($placeholder.Replace('...', 'changed-placeholder')))) {
                [System.IO.File]::WriteAllText($path, $content)
                $rejected = $false
                try { & $scanner -Root $root | Out-Null }
                catch { $rejected = $true }
                if (-not $rejected) { throw "Privacy scanner accepted changed or additional key content: $prefix$asset" }
            }
        } finally {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }
    $unknownPath = Join-Path $root 'unknown-asset.js'
    try {
        [System.IO.File]::WriteAllText($unknownPath, $placeholder)
        $rejected = $false
        try { & $scanner -Root $root | Out-Null }
        catch { $rejected = $true }
        if (-not $rejected) { throw 'Privacy scanner accepted a key header at an unaudited path.' }
    } finally {
        Remove-Item -LiteralPath $unknownPath -Force -ErrorAction SilentlyContinue
    }
}

try {
    [System.IO.File]::WriteAllText((Join-Path $root 'public.json'), '{}')
    & $scanner -Root $root | Out-Null
    Assert-ScannerRejects 'wechat.json'
    Assert-ScannerRejects 'qq-local.json'
    Assert-ScannerRejects 'onebot11.json'
    foreach ($databaseFixture in @(
        'chat.db', 'chat.db3', 'chat.s3db', 'chat.sl3', 'chat.sqlite', 'chat.sqlite3',
        'chat.mdb', 'chat.accdb', 'chat.db-wal', 'chat.db-shm', 'chat.db-journal',
        'chat.sqlite-wal', 'chat.sqlite-shm', 'chat.sqlite-journal',
        'chat.sqlite3-wal', 'chat.sqlite3-shm', 'chat.sqlite3-journal'
    )) {
        Assert-ScannerRejects $databaseFixture
    }
    Assert-ScannerRejects 'provider.pyc'
    Assert-ScannerRejects 'private.key'
    Assert-ScannerRejects 'wecom-bot.credentials'
    Assert-ScannerRejects 'attachment.blob'
    $outbox = Join-Path $root 'wecom-bot-outbox'
    New-Item -ItemType Directory -Path $outbox | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $outbox 'fixture.txt'), 'synthetic attachment')
    $outboxRejected = $false
    try { & $scanner -Root $root | Out-Null }
    catch { $outboxRejected = $true }
    Remove-Item -LiteralPath $outbox -Recurse -Force
    if (-not $outboxRejected) { throw 'Privacy scanner accepted the WeCom bot media outbox directory.' }
    Assert-ScannerRejectsProfilePaths
    Assert-ScannerRejectsRedirectedAppData
    Assert-ScannerReadsTabularExports
    Assert-SourcePackagerExcludesTabularExports
    Assert-ScannerAuditedPlaceholder
    Write-Output 'PASS: package privacy scanner rejects private artifacts and paths; only the exact audited upstream input placeholder is excluded.'
} finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
