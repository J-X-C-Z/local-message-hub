param(
    [string]$OutputPath = '',
    [string]$Python = 'python',
    [switch]$OmitQceRuntime,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($env:OS -ne 'Windows_NT') { throw 'This package builder requires Windows x64.' }
if (-not $OutputPath) { $OutputPath = Join-Path $repo 'output/windows/LocalMessageHub-portable' }
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $OutputPath) { throw "Output already exists; choose a new path: $OutputPath" }

$pythonCommand = Get-Command $Python -ErrorAction Stop
$pythonVersion = & $pythonCommand.Source -c 'import sys; print("%d.%d" % sys.version_info[:2])'
if ($LASTEXITCODE -ne 0 -or $pythonVersion.Trim() -ne '3.12') {
    throw "Python 3.12 is required; found $pythonVersion"
}
$node = Get-Command npm.cmd -ErrorAction Stop

function Invoke-PackageSmokeTest([string]$PackageRoot) {
    $root = [System.IO.Path]::GetFullPath($PackageRoot)
    $exe = Join-Path $root 'LocalMessageHub.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Portable executable missing: $exe" }
    $smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-portable-smoke-' + [guid]::NewGuid().ToString('N'))
    $storage = Join-Path $smokeRoot 'isolated-storage'
    $stdout = Join-Path $smokeRoot 'server.stdout.log'
    $stderr = Join-Path $smokeRoot 'server.stderr.log'
    New-Item -ItemType Directory -Path $storage -Force | Out-Null
    $port = Get-Random -Minimum 20000 -Maximum 50000
    $arguments = '--port {0} --storage "{1}" --no-autosync --no-browser' -f $port, $storage
    $process = $null
    try {
        $process = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $root `
            -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $url = $null
        for ($attempt = 0; $attempt -lt 80; $attempt++) {
            if ($process.HasExited) {
                $outText = if (Test-Path $stdout) { Get-Content -Raw $stdout } else { '' }
                $errText = if (Test-Path $stderr) { Get-Content -Raw $stderr } else { '' }
                throw "Portable Hub exited during startup. stdout=$outText stderr=$errText"
            }
            $statePath = Join-Path $storage 'server.json'
            if (Test-Path -LiteralPath $statePath -PathType Leaf) {
                try {
                    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
                    if ($state.url -match '^http://127\.0\.0\.1:\d+$') { $url = $state.url; break }
                } catch { }
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $url) { throw 'Portable Hub did not publish a loopback URL within 20 seconds.' }
        $page = Invoke-WebRequest -Uri "$url/" -UseBasicParsing -TimeoutSec 5
        if ($page.StatusCode -ne 200 -or $page.Content -notmatch '<html lang="zh-CN">') { throw 'Packaged WebUI was unavailable.' }
        $icons = Invoke-WebRequest -Uri "$url/vendor/lucide.js" -UseBasicParsing -TimeoutSec 5
        if ($icons.StatusCode -ne 200 -or $icons.Content.Length -lt 1000) { throw 'Packaged Lucide asset was unavailable.' }
        $sources = Invoke-RestMethod -Uri "$url/api/sources" -TimeoutSec 5
        if (@($sources.sources).Count -ne 3) { throw 'Expected three platform entries in the portable package.' }
        $settings = Invoke-RestMethod -Uri "$url/api/settings" -TimeoutSec 5
        if ([System.IO.Path]::GetFullPath($settings.storage_dir) -ne [System.IO.Path]::GetFullPath($storage)) {
            throw 'Portable executable did not use isolated temporary storage.'
        }
        $mcp = Invoke-RestMethod -Uri "$url/api/mcp" -TimeoutSec 5
        if (-not $mcp.available -or $mcp.mode -ne 'read-only') { throw 'Read-only MCP endpoint was unavailable.' }
        Write-Output 'PASS: bundled executable served the UI and Lucide without installed Python or Node.'
        Write-Output 'PASS: three source entries, isolated storage, and read-only MCP endpoint.'
    } finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit(5000) | Out-Null
        }
        if (Test-Path -LiteralPath $smokeRoot) { Remove-Item -LiteralPath $smokeRoot -Recurse -Force }
    }
}

$temp = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-portable-build-' + [guid]::NewGuid().ToString('N'))
$stage = Join-Path $temp 'stage'
$venv = Join-Path $temp 'venv'
$dist = Join-Path $temp 'dist'
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$excludedNames = @('.git', '.local', '.runtime', 'runtime', 'venv', '.venv', '__pycache__', 'node_modules', 'output')
$excludedFiles = @('AGENTS.md', 'TASK.md', 'CLAUDE.md', 'CODEX.md',
                   'server.json', 'settings.json', 'wechat.json', 'wecom.json', 'security.json')
$excludedExtensions = @('.db', '.sqlite', '.sqlite3', '.pyc', '.log', '.pem', '.key')
function Copy-PublicTree([string]$Source, [string]$Destination, [switch]$PreserveRuntimeTree) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $blockedNames = $excludedNames
    if ($PreserveRuntimeTree) { $blockedNames = @($excludedNames | Where-Object { $_ -ne 'runtime' }) }
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
        if ($item.PSIsContainer) {
            if ($blockedNames -contains $item.Name) { continue }
            Copy-PublicTree $item.FullName (Join-Path $Destination $item.Name) -PreserveRuntimeTree:$PreserveRuntimeTree
            continue
        }
        if ($excludedFiles -contains $item.Name -or $excludedExtensions -contains $item.Extension.ToLowerInvariant()) { continue }
        Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $Destination $item.Name)
    }
}

try {
    foreach ($directory in @('hub', 'webui', 'integrations/wechat', 'integrations/wecom', 'integrations/telegram')) {
        Copy-PublicTree (Join-Path $repo $directory) (Join-Path $stage $directory)
    }
    foreach ($file in @('integrations/qq/provider.py', 'integrations/qq/README.md',
                        'scripts/windows_portable.py', 'scripts/windows_mcp.py')) {
        $destination = Join-Path $stage $file
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $repo $file) -Destination $destination
    }
    Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination (Join-Path $stage 'LICENSE')
    Copy-Item -LiteralPath (Join-Path $repo 'UPSTREAM.md') -Destination (Join-Path $stage 'UPSTREAM.md')

    $pythonVenv = Join-Path $venv 'Scripts/python.exe'
    & $pythonCommand.Source -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated packaging environment.' }
    & $pythonVenv -m pip install --disable-pip-version-check -r (Join-Path $repo 'requirements-windows-bundle.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Installing pinned Python bundle dependencies failed.' }

    $webui = Join-Path $stage 'webui'
    & $node.Source ci --offline --prefix $webui
    if ($LASTEXITCODE -ne 0) { throw 'Installing the locked WebUI dependency failed.' }

    if (-not $OmitQceRuntime) {
        $qceZip = Join-Path $temp 'NapCat-QCE-Windows-x64-v6.3.2.zip'
        $qceUrl = 'https://github.com/shuakami/qq-chat-exporter/releases/download/v6.3.2/NapCat-QCE-Windows-x64-v6.3.2.zip'
        Invoke-WebRequest -Uri $qceUrl -OutFile $qceZip
        $archiveHash = (Get-FileHash -LiteralPath $qceZip -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($archiveHash -ne '3fc093ba2332b97ac17e16160d5baa6ffe0a8def6c394d515a94777fe0ca492c') {
            throw "QCE v6.3.2 archive SHA-256 mismatch: $archiveHash"
        }
        $unpack = Join-Path $temp 'qce-unpacked'
        Expand-Archive -LiteralPath $qceZip -DestinationPath $unpack
        $serverExe = Get-ChildItem -LiteralPath $unpack -Filter qce-server.exe -File -Recurse | Select-Object -First 1
        $bootExe = Get-ChildItem -LiteralPath $unpack -Filter NapCatWinBootMain.exe -File -Recurse | Select-Object -First 1
        if (-not $serverExe -or -not $bootExe) { throw 'Official QCE archive is missing required shell executables.' }
        $qceRoot = $bootExe.Directory.FullName
        if (-not (Test-Path -LiteralPath (Join-Path $qceRoot 'qce-server.exe') -PathType Leaf)) {
            throw 'QCE server and NapCat bootloader were not located in the same official shell folder.'
        }
        $binaryHash = (Get-FileHash -LiteralPath $serverExe.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($binaryHash -ne '066d9ff15c3e6cb7efe23acc564b5b7b34d6da2849fec77a7dcc890290bb7745') {
            throw "QCE server SHA-256 mismatch: $binaryHash"
        }
        $runtimeTarget = Join-Path $stage '.runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64'
        New-Item -ItemType Directory -Path (Split-Path -Parent $runtimeTarget) -Force | Out-Null
        Copy-Item -LiteralPath $qceRoot -Destination $runtimeTarget -Recurse
        $licenseSource = Join-Path $repo 'integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2/LICENSE'
        if (-not (Test-Path -LiteralPath $licenseSource -PathType Leaf)) {
            throw 'QCE GPL-3.0 license text is missing from the verified upstream source tree.'
        }
        Copy-Item -LiteralPath $licenseSource -Destination (Join-Path $stage 'COPYING-QCE-GPL-3.0.txt')
        $sourceTarget = Join-Path $stage 'licenses/qq-chat-exporter-6.3.2-source'
        Copy-PublicTree (Join-Path $repo 'integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2') $sourceTarget -PreserveRuntimeTree
    }

    $entry = Join-Path $stage 'scripts/windows_portable.py'
    $mcpEntry = Join-Path $stage 'scripts/windows_mcp.py'
    $pyinstaller = Join-Path $venv 'Scripts/pyinstaller.exe'
    $common = @(
        '--noconfirm', '--clean', '--onedir', '--console',
        '--distpath', $dist, '--workpath', (Join-Path $temp 'work'), '--specpath', (Join-Path $temp 'spec'),
        '--paths', $stage,
        '--add-data', ((Join-Path $stage 'hub') + ';hub'),
        '--add-data', ((Join-Path $stage 'webui') + ';webui'),
        '--add-data', ((Join-Path $stage 'integrations') + ';integrations'),
        '--hidden-import', 'integrations.wechat.provider',
        '--hidden-import', 'integrations.wecom.adapter',
        '--hidden-import', 'integrations.qq.provider',
        '--hidden-import', 'integrations.telegram.adapter',
        '--collect-all', 'qrcode', '--collect-all', 'pilk_nogil', '--collect-all', 'Crypto'
    )
    if (-not $OmitQceRuntime) { $common += @('--add-data', ((Join-Path $stage '.runtime') + ';.runtime')) }
    & $pyinstaller @common --name LocalMessageHub $entry
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed to build the Hub executable.' }
    & $pyinstaller @common --name LocalMessageHub-MCP $mcpEntry
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed to build the MCP executable.' }

    $payload = Join-Path $dist 'LocalMessageHub'
    $mcpPayload = Join-Path $payload 'mcp'
    New-Item -ItemType Directory -Path $mcpPayload -Force | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $dist 'LocalMessageHub-MCP') -Force |
        Move-Item -Destination $mcpPayload
    foreach ($notice in @('LICENSE', 'UPSTREAM.md', 'COPYING-QCE-GPL-3.0.txt')) {
        $noticePath = Join-Path $stage $notice
        if (Test-Path -LiteralPath $noticePath -PathType Leaf) {
            Copy-Item -LiteralPath $noticePath -Destination (Join-Path $payload $notice)
        }
    }
    $sourceNotice = Join-Path $stage 'licenses/qq-chat-exporter-6.3.2-source'
    if (Test-Path -LiteralPath $sourceNotice -PathType Container) {
        $licenseDirectory = Join-Path $payload 'licenses'
        New-Item -ItemType Directory -Path $licenseDirectory -Force | Out-Null
        Copy-Item -LiteralPath $sourceNotice -Destination $licenseDirectory -Recurse
    }
    @'
@echo off
setlocal
cd /d "%~dp0"
"%~dp0LocalMessageHub.exe" %*
'@ | Set-Content -LiteralPath (Join-Path $payload 'START-LocalMessageHub.cmd') -Encoding ASCII
    @'
@echo off
setlocal
cd /d "%~dp0"
"%~dp0mcp\LocalMessageHub-MCP.exe" %*
'@ | Set-Content -LiteralPath (Join-Path $payload 'START-LocalMessageHub-MCP.cmd') -Encoding ASCII
    @'
Local Message Hub portable Windows build

Run START-LocalMessageHub.cmd to start the local WebUI service. It uses a bundled
Python runtime and bundled UI assets; starting the app does not run pip, npm, or
download software. Local Hub database and settings default to:
%LOCALAPPDATA%\LocalMessageHub

Run START-LocalMessageHub-MCP.cmd from an MCP client configuration to start the
standalone stdio server. Do not start it in a terminal that expects ordinary text
output; stdout is reserved for the MCP protocol.

This build includes the official qq-chat-exporter/NapCat QCE v6.3.2 Windows x64
shell after archive and executable SHA-256 checks. The runtime is copied to the
user-local data directory on first launch and is never copied into QQ's install.
QQ login and history still require the user's QQ client and an explicit QR login.
See UPSTREAM.md and COPYING-QCE-GPL-3.0.txt for source provenance and notices.

The portable build is unsigned and not an NSIS installer. Do not publish a build
without reviewing the included upstream licenses and applicable source-offer duties.
'@ | Set-Content -LiteralPath (Join-Path $payload 'README-PORTABLE.txt') -Encoding UTF8

    $outputParent = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    Move-Item -LiteralPath $payload -Destination $OutputPath
    if (-not $SkipSmokeTest) {
        Invoke-PackageSmokeTest $OutputPath
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Portable package smoke test failed.' }
    }
    Write-Output "Portable Windows build: $OutputPath"
    Write-Output 'Runtime startup does not invoke pip, npm, or a network installer.'
    if (-not $OmitQceRuntime) { Write-Output 'Included official QCE v6.3.2 shell and matching source after SHA-256 verification.' }
    else { Write-Output 'QQ runtime deliberately omitted by -OmitQceRuntime.' }
} finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
