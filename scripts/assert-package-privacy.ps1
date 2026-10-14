param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [switch]$GitVisible
)

$ErrorActionPreference = 'Stop'
$rootPath = [System.IO.Path]::GetFullPath($Root)
if (-not (Test-Path -LiteralPath $rootPath -PathType Container)) {
    throw 'Package privacy scan root is not a directory.'
}

$privateRoots = @($env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA) |
    Where-Object { $_ } |
    ForEach-Object { [System.IO.Path]::GetFullPath($_).TrimEnd('\', '/') } |
    Select-Object -Unique
$normalizedRoots = @($privateRoots | ForEach-Object {
    [regex]::Replace($_.Replace('\', '/'), '/{2,}', '/')
})
$forbiddenFileNames = @('server.json', 'settings.json', 'wechat.json', 'wecom.json', 'qq-local.json',
                        'security.json', 'onebot11.json', 'napcat.json', 'wecom-bot.credentials',
                        '.env', '.env.local', 'credentials.json', 'cookies.json', 'account.json')
$forbiddenSuffixes = @('.db', '.db3', '.s3db', '.sl3', '.sqlite', '.sqlite3', '.mdb', '.accdb',
                       '.db-wal', '.db-shm', '.db-journal', '.sqlite-wal', '.sqlite-shm', '.sqlite-journal',
                       '.sqlite3-wal', '.sqlite3-shm', '.sqlite3-journal', '.pyc', '.log', '.pem', '.key', '.blob')
$textExtensions = @('.cmd', '.css', '.html', '.js', '.json', '.jsonl', '.md',
                    '.ps1', '.py', '.toml', '.txt', '.csv', '.tsv', '.xml', '.yaml', '.yml',
                    '.c', '.cjs', '.cfg', '.cpp', '.cs', '.env', '.h', '.hpp',
                    '.ini', '.mjs', '.rs', '.sql', '.ts', '.tsx')
$secretPatterns = @(
    '(?i)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----',
    '\bgh[pousr]_[A-Za-z0-9_]{20,}\b',
    '\bgithub_pat_[A-Za-z0-9_]{20,}\b',
    '\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    '\bxox[baprs]-[A-Za-z0-9-]{20,}\b'
)

if ($GitVisible) {
    $gitFiles = @(& git -C $rootPath -c core.quotepath=false ls-files --cached --others --exclude-standard 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Could not enumerate Git-visible repository files for privacy scanning.' }
    $files = @($gitFiles | ForEach-Object {
        $path = Join-Path $rootPath $_
        if (Test-Path -LiteralPath $path -PathType Leaf) { Get-Item -LiteralPath $path }
    })
} else {
    $files = @(Get-ChildItem -LiteralPath $rootPath -File -Recurse -Force)
}

foreach ($file in $files) {
    $relativeRoot = $rootPath.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $relative = $file.FullName.Substring($relativeRoot.Length)
    $normalizedRelative = $relative.Replace('\', '/')
    if (@($normalizedRelative -split '/' | Where-Object { $_ -ieq 'wecom-bot-outbox' }).Count -gt 0) {
        throw "Privacy scan found a private media outbox directory: $relative"
    }
    $isOfficialRuntimeTemplate = $normalizedRelative -match '^(\.runtime/qq/v6\.3\.2/shell/NapCat-QCE-Windows-x64/config|_internal/\.runtime/qq/v6\.3\.2/shell/NapCat-QCE-Windows-x64/config|mcp/_internal/\.runtime/qq/v6\.3\.2/shell/NapCat-QCE-Windows-x64/config|licenses/qq-chat-exporter-6\.3\.2-source/docker/config|integrations/qq/upstream/expanded/qq-chat-exporter-6\.3\.2/docker/config)/(onebot11|napcat)\.json$'
    if (($forbiddenFileNames -contains $file.Name.ToLowerInvariant()) -and -not $isOfficialRuntimeTemplate) {
        throw "Privacy scan found a forbidden account or runtime config file: $relative"
    }
    $lowerName = $file.Name.ToLowerInvariant()
    if (@($forbiddenSuffixes | Where-Object { $lowerName.EndsWith($_, [System.StringComparison]::Ordinal) }).Count -gt 0) {
        throw "Privacy scan found a forbidden local or runtime file: $relative"
    }
    if ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Privacy scan refuses a repository symlink: $relative"
    }
    if ($textExtensions -notcontains $file.Extension.ToLowerInvariant()) { continue }
    if ($file.Length -gt 8MB) { throw "Refusing to skip large text file during privacy scan: $($file.Name)" }
    $content = [System.IO.File]::ReadAllText($file.FullName)
    $normalizedContent = [regex]::Replace($content.Replace('\', '/'), '/{2,}', '/')
    foreach ($privateRoot in $normalizedRoots) {
        if ($normalizedContent.IndexOf($privateRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        throw "Privacy scan found a path from the current user's profile or local data directory: $relative"
        }
    }
    if ($normalizedRelative -cmatch '^(?:_internal/|mcp/_internal/)?\.runtime/qq/v6\.3\.2/shell/NapCat-QCE-Windows-x64/static/assets/index-Cgjz17KL\.js$') {
        # The audited upstream TLS input uses a literal, empty PEM placeholder.
        $placeholder = 'placeholder:`-----BEGIN ' + 'PRIVATE KEY-----' + "`n...`n" + '-----END ' + 'PRIVATE KEY-----`'
        $content = $content.Replace($placeholder, '')
    }
    foreach ($pattern in $secretPatterns) {
        if ([regex]::IsMatch($content, $pattern)) {
            throw "Privacy scan found a high-confidence credential pattern ($pattern): $relative"
        }
    }
}

if ($GitVisible) {
    Write-Output 'PASS: Git-visible files contain no known account configs, current-user paths, or supported high-confidence credential formats.'
} else {
    Write-Output 'PASS: package text contains no current-user profile or local-data paths.'
}
