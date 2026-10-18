param(
    [string]$OutputPath = '',
    [string]$Python = 'python',
    [string]$SmokePackageRoot = '',
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
function Invoke-PackageSmokeTest([string]$PackageRoot, [string]$PythonCommand) {
    $rootPath = [System.IO.Path]::GetFullPath($PackageRoot)
    if (-not (Test-Path -LiteralPath (Join-Path $rootPath 'hub/server.py') -PathType Leaf)) {
        throw "Package is missing hub/server.py: $rootPath"
    }
    $python = (Get-Command $PythonCommand -ErrorAction Stop).Source
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-package-smoke-' + [guid]::NewGuid().ToString('N'))
    $storage = Join-Path $temp 'storage'
    $stdout = Join-Path $temp 'server.stdout.log'
    $stderr = Join-Path $temp 'server.stderr.log'
    New-Item -ItemType Directory -Path $storage -Force | Out-Null
    if (@(Get-ChildItem -LiteralPath $storage -Force).Count -ne 0) { throw 'Package smoke storage was not empty before startup.' }
    $arguments = '-m hub.server --port 0 --storage "{0}" --no-autosync' -f $storage
    $smokeEnvironment = @{
        LOCALAPPDATA = $temp
        APPDATA = Join-Path $temp 'appdata'
        USERPROFILE = Join-Path $temp 'userprofile'
        LMH_STORAGE_DIR = $storage
        WECOM_LOCAL_DIR = Join-Path $storage 'integrations/wecom'
        WECHATAUTO_KEYS_DIR = Join-Path $storage 'integrations/wechat/keys'
        QCE_SECURITY_FILE = Join-Path $storage 'qq/security.json'
        QCE_BUNDLE_DIR = Join-Path $temp 'LocalMessageHub/runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64'
        QCE_BASE_URL = 'http://127.0.0.1:0'
        PYTHONPATH = $null
        PYTHONHOME = $null
        PYTHONUSERBASE = $null
        VIRTUAL_ENV = $null
        PYTHONNOUSERSITE = '1'
    }
    $originalSmokeEnvironment = @{}
    foreach ($name in $smokeEnvironment.Keys) {
        $originalSmokeEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, 'Process')
    }
    $process = $null
    try {
        foreach ($name in $smokeEnvironment.Keys) {
            [System.Environment]::SetEnvironmentVariable($name, $smokeEnvironment[$name], 'Process')
        }
        $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $rootPath `
            -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $statePath = Join-Path $storage 'server.json'
        $loopbackUrlPattern = '^http://127\.0\.0\.1:(\d+)$'
        $url = $null
        for ($attempt = 0; $attempt -lt 80; $attempt++) {
            if ($process.HasExited) {
                $outText = if (Test-Path $stdout) { Get-Content -Raw $stdout } else { '' }
                $errText = if (Test-Path $stderr) { Get-Content -Raw $stderr } else { '' }
                throw "Hub exited during startup. stdout=$outText stderr=$errText"
            }
            if (Test-Path -LiteralPath $statePath -PathType Leaf) {
                try {
                    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
                    if ([long]$state.pid -gt 0 -and
                        $state.url -match $loopbackUrlPattern -and [int]$Matches[1] -gt 0) {
                        $url = $state.url
                        break
                    }
                } catch {
                }
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $url) {
            $outText = if (Test-Path $stdout) { Get-Content -Raw $stdout } else { '' }
            $errText = if (Test-Path $stderr) { Get-Content -Raw $stderr } else { '' }
            if ($outText.Length -gt 2000) { $outText = $outText.Substring($outText.Length - 2000) }
            if ($errText.Length -gt 2000) { $errText = $errText.Substring($errText.Length - 2000) }
            throw "Hub did not publish a loopback server URL within 20 seconds. stdout=$outText stderr=$errText"
        }
        $page = Invoke-WebRequest -Uri "$url/" -UseBasicParsing -TimeoutSec 5
        if ($page.StatusCode -ne 200 -or $page.Content -notmatch '<html lang="zh-CN">') {
            throw 'Packaged WebUI root did not return the expected page.'
        }
        $sources = Invoke-RestMethod -Uri "$url/api/sources" -TimeoutSec 5
        if (@($sources.sources).Count -ne 3) { throw 'Expected the three platform source entries.' }
        $settings = Invoke-RestMethod -Uri "$url/api/settings" -TimeoutSec 5
        if ([System.IO.Path]::GetFullPath($settings.storage_dir) -ne [System.IO.Path]::GetFullPath($storage)) {
            throw 'Hub did not use the isolated temporary storage directory.'
        }
        $conversations = Invoke-RestMethod -Uri "$url/api/conversations?platform=all&limit=1" -TimeoutSec 5
        if ([int]$conversations.total -ne 0 -or @($conversations.items).Count -ne 0) {
            throw 'Package smoke storage unexpectedly contained conversations.'
        }
        $mcp = Invoke-RestMethod -Uri "$url/api/mcp" -TimeoutSec 5
        if (-not $mcp.available -or $mcp.mode -ne 'read-only') {
            throw 'MCP endpoint did not report the expected available, read-only configuration.'
        }
        Write-Output "PASS: extracted package served the WebUI and three source entries at $url"
        Write-Output 'PASS: isolated storage and read-only MCP endpoint responded'
    } finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit(5000) | Out-Null
        }
        foreach ($name in $smokeEnvironment.Keys) {
            [System.Environment]::SetEnvironmentVariable($name, $originalSmokeEnvironment[$name], 'Process')
        }
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
    }
}

$repo = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($env:OS -ne 'Windows_NT') { throw 'This packaging script is intended for Windows.' }
if ($SmokePackageRoot) {
    Invoke-PackageSmokeTest -PackageRoot $SmokePackageRoot -PythonCommand $Python
    return
}
if (-not $OutputPath) { $OutputPath = Join-Path $repo 'output/windows/LocalMessageHub-source.zip' }
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)

$requiredFiles = @(
    'START.cmd', 'start_hub.py', 'LICENSE', 'UPSTREAM.md',
    'scripts/package-windows-source.ps1',
    'scripts/build-qce-windows.ps1',
    'scripts/assert-package-privacy.ps1',
    'scripts/test-package-privacy.ps1',
    'scripts/benchmark-memory.py',
    'hub/server.py', 'hub/store.py', 'hub/mcp_server.py',
    'hub/windows_notifications.py', 'hub/windows_toast.ps1',
    'integrations/wecom/bot_credentials.py', 'integrations/wecom/bot_media.py',
    'integrations/wecom/bot_sender.py',
    'integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2/qq-chat-export-server/Cargo.lock',
    'integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2/LICENSE',
    'webui/index.html', 'webui/app.js', 'webui/style.css',
    'webui/package.json', 'webui/package-lock.json',
    'integrations/wechat/requirements.txt', 'integrations/wechat/provider.py',
    'integrations/wecom/adapter.py', 'integrations/qq/provider.py', 'integrations/qq/sender.py',
    'integrations/qq/local_provider.py', 'integrations/qq/local_wal.py', 'integrations/qq/key_reader.py',
    'integrations/qq/key_store.py', 'integrations/qq/send_target.py',
    'integrations/telegram/adapter.py',
    'docs/LOCAL_HUB.md', 'docs/mcp-contract.md', 'docs/windows-package.md',
    'docs/avatar-and-attachments.md', 'docs/roadmap.md', 'docs/weflow-gap-matrix.md',
    'docs/performance.md', 'docs/qq-local.md'
)
foreach ($relativePath in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $repo $relativePath) -PathType Leaf)) {
        throw "Required source file is missing: $relativePath"
    }
}

$forbiddenDirectoryNames = @(
    '.git', '.local', '.runtime', 'runtime', '.cache', 'cache', 'caches', 'venv', '.venv', 'node_modules',
    '__pycache__', 'output', '.playwright', '.playwright-cli', 'target', 'wecom-bot-outbox'
)
$forbiddenFileNames = @('server.json', 'settings.json', 'wechat.json', 'wecom.json',
                        'security.json', 'onebot11.json', 'napcat.json', 'wecom-bot.credentials')
$forbiddenExtensions = @('.db', '.sqlite', '.sqlite3', '.db-wal', '.db-shm', '.sqlite-wal',
                         '.sqlite-shm', '.sqlite3-wal', '.sqlite3-shm', '.pyc', '.log', '.pem', '.key', '.blob',
                         '.csv', '.tsv')
$includePaths = @(
    'START.cmd', 'start_hub.py', 'LICENSE', 'UPSTREAM.md',
    'hub', 'webui',
    'integrations/wechat', 'integrations/wecom',
    'integrations/qq/provider.py', 'integrations/qq/sender.py',
    'integrations/qq/local_provider.py', 'integrations/qq/local_wal.py', 'integrations/qq/key_reader.py',
    'integrations/qq/key_store.py', 'integrations/qq/send_target.py',
    'integrations/qq/README.md',
    'integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2',
    'integrations/telegram',
    'scripts/package-windows-source.ps1', 'scripts/build-qce-windows.ps1', 'scripts/assert-package-privacy.ps1',
    'scripts/test-package-privacy.ps1',
    'scripts/benchmark-memory.py',
    'docs/LOCAL_HUB.md', 'docs/mcp-contract.md', 'docs/windows-package.md',
    'docs/avatar-and-attachments.md', 'docs/roadmap.md', 'docs/weflow-gap-matrix.md',
    'docs/performance.md', 'docs/qq-local.md'
)

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-package-stage-' + [guid]::NewGuid().ToString('N'))
$payloadRoot = Join-Path $tempRoot 'payload'
New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null
$copied = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

function Copy-SourceFile([string]$SourcePath) {
    $item = Get-Item -LiteralPath $SourcePath -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return }
    if ($forbiddenFileNames -contains $item.Name) { return }
    if ($forbiddenExtensions -contains $item.Extension.ToLowerInvariant()) { return }

    $repoPrefix = $repo.TrimEnd('\') + '\'
    if (-not $item.FullName.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to package a path outside the repository: $SourcePath"
    }
    $relative = $item.FullName.Substring($repoPrefix.Length)
    $destination = Join-Path $payloadRoot $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $item.FullName -Destination $destination
    [void]$copied.Add($relative.Replace('\', '/'))
}

function Copy-SourceTree([string]$SourcePath) {
    Assert-PackageSourceTreeRoot -SourcePath $SourcePath
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push($SourcePath)
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        foreach ($child in Get-ChildItem -LiteralPath $current -Force) {
            if ($forbiddenDirectoryNames -contains $child.Name) { continue }
            if (($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
            if ($child.PSIsContainer) { $pending.Push($child.FullName) }
            else { Copy-SourceFile $child.FullName }
        }
    }
}

function Assert-PackageSourceTreeRoot([string]$SourcePath) {
    $item = Get-Item -LiteralPath $SourcePath -Force
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Packaging source tree root must be a real directory: $SourcePath"
    }
}

try {
    foreach ($relativePath in $includePaths) {
        $sourcePath = Join-Path $repo $relativePath
        if (-not (Test-Path -LiteralPath $sourcePath)) { throw "Packaging input is missing: $relativePath" }
        if ((Get-Item -LiteralPath $sourcePath -Force).PSIsContainer) { Copy-SourceTree $sourcePath }
        else { Copy-SourceFile $sourcePath }
    }

    $missing = @($requiredFiles | Where-Object { -not $copied.Contains($_) })
    if ($missing.Count -gt 0) { throw "Portable bundle omitted required files: $($missing -join ', ')" }

    & (Join-Path $payloadRoot 'scripts/assert-package-privacy.ps1') -Root $payloadRoot
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Source bundle privacy scan failed.' }

    $packageReadme = @'
Local Message Hub - Windows portable source bundle

This bundle contains the Local Message Hub application source and a clean-source smoke check.
It is not a self-contained installer. First launch requires Windows, Python 3.12, Node.js/npm,
and network access so START.cmd can bootstrap Python and Lucide dependencies.

The QQ NapCat/QCE runtime is not included. QQ login/history is unavailable until a separately
obtained, license-reviewed runtime is placed in the configured local runtime directory.
Do not add account databases, credentials, chat exports, .local, .runtime, venvs, or node_modules
to a public package. See UPSTREAM.md and docs/windows-package.md for provenance and boundaries.

Run scripts/package-windows-source.ps1 -SmokePackageRoot <extracted-directory> from PowerShell
to check the bundle using temporary empty storage with automatic synchronization disabled.
'@
    Set-Content -LiteralPath (Join-Path $payloadRoot 'PORTABLE-PACKAGE.txt') -Value $packageReadme -Encoding UTF8

    $outputParent = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    if (Test-Path -LiteralPath $OutputPath) { throw "Output already exists; choose a new path: $OutputPath" }

    Add-Type -AssemblyName System.IO.Compression
    $stagedArchive = Join-Path $tempRoot 'LocalMessageHub-source.zip'
    $archiveStream = [System.IO.File]::Open($stagedArchive, [System.IO.FileMode]::CreateNew)
    $archive = [System.IO.Compression.ZipArchive]::new($archiveStream, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        $paths = [string[]]@(Get-ChildItem -LiteralPath $payloadRoot -Recurse -File | ForEach-Object FullName)
        [Array]::Sort($paths, [System.StringComparer]::Ordinal)
        foreach ($path in $paths) {
            $relativePath = $path.Substring($payloadRoot.TrimEnd('\', '/').Length + 1).Replace('\', '/')
            $entry = $archive.CreateEntry($relativePath, [System.IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = [DateTimeOffset]::new(1980, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
            $entryStream = $entry.Open()
            try {
                $inputStream = [System.IO.File]::OpenRead($path)
                try { $inputStream.CopyTo($entryStream) }
                finally { $inputStream.Dispose() }
            } finally {
                $entryStream.Dispose()
            }
        }
    } finally {
        $archive.Dispose()
        $archiveStream.Dispose()
    }
    $archive = [System.IO.Compression.ZipFile]::OpenRead($stagedArchive)
    try {
        foreach ($entry in $archive.Entries) {
            $segments = $entry.FullName -split '/'
            if (@($segments | Where-Object { $forbiddenDirectoryNames -contains $_ }).Count -gt 0) {
                throw "Forbidden runtime/private directory found in archive: $($entry.FullName)"
            }
            if ($forbiddenFileNames -contains (Split-Path -Leaf $entry.FullName)) {
                throw "Forbidden local state file found in archive: $($entry.FullName)"
            }
            if (@($segments | Where-Object { $forbiddenDirectoryNames -contains $_ }).Count -gt 0) {
                throw "Forbidden private-data directory found in archive: $($entry.FullName)"
            }
            $extension = [System.IO.Path]::GetExtension($entry.FullName).ToLowerInvariant()
            if ($forbiddenExtensions -contains $extension) {
                throw "Forbidden local/runtime file found in archive: $($entry.FullName)"
            }
        }
    } finally {
        $archive.Dispose()
    }

    if (-not $SkipSmokeTest) {
        $extractedRoot = Join-Path $tempRoot 'extracted'
        [System.IO.Compression.ZipFile]::ExtractToDirectory($stagedArchive, $extractedRoot)
        & (Join-Path $extractedRoot 'scripts/package-windows-source.ps1') `
            -SmokePackageRoot $extractedRoot -Python $Python
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "Smoke test failed with exit code $LASTEXITCODE" }
    }

    if (Test-Path -LiteralPath $OutputPath) { throw "Output appeared during the build; refusing to overwrite it: $OutputPath" }
    Move-Item -LiteralPath $stagedArchive -Destination $OutputPath

    Write-Output "Portable source bundle: $OutputPath"
    Write-Output "Included files: $($copied.Count + 1)"
    Write-Output 'Ignored local data and runtime directories were excluded before traversal.'
} finally {
    if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force }
}
