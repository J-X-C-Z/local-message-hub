param(
    [string]$OutputPath = '',
    [string]$Python = 'python',
    [string]$QceServerPath = '',
    [string]$QceServerSha256 = '',
    [switch]$OmitQceRuntime,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($env:OS -ne 'Windows_NT') { throw 'This package builder requires Windows x64.' }
function Get-VerifiedPrebuiltQceHash([string]$Path, [string]$Sha256) {
    if ([bool]$Path -ne [bool]$Sha256) { throw 'Prebuilt QCE requires both -QceServerPath and -QceServerSha256.' }
    if (-not $Path) { return '' }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Prebuilt QCE server does not exist: $Path" }
    if ($Sha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw 'Prebuilt QCE SHA-256 must contain exactly 64 hexadecimal characters.'
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256.ToLowerInvariant()) {
        throw "Prebuilt QCE SHA-256 mismatch: expected $($Sha256.ToLowerInvariant()), found $actual."
    }
    return $actual
}
function Add-ApprovedQceHash([string]$ProviderText, [string]$Sha256) {
    if ($Sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'QCE provider allowlist digest must be a 64-character SHA-256.' }
    $anchor = '"066d9ff15c3e6cb7efe23acc564b5b7b34d6da2849fec77a7dcc890290bb7745"'
    $first = $ProviderText.IndexOf($anchor, [System.StringComparison]::Ordinal)
    if ($first -lt 0 -or $first -ne $ProviderText.LastIndexOf($anchor, [System.StringComparison]::Ordinal)) {
        throw 'Could not locate exactly one QCE hash allowlist anchor in the staged provider.'
    }
    return $ProviderText.Replace($anchor, "$anchor,`r`n    `"$($Sha256.ToLowerInvariant())`"")
}
$prebuiltQceHash = Get-VerifiedPrebuiltQceHash -Path $QceServerPath -Sha256 $QceServerSha256
if ($OmitQceRuntime -and $QceServerPath) { throw 'Prebuilt QCE cannot be combined with -OmitQceRuntime.' }
if (-not $OutputPath) { $OutputPath = Join-Path $repo 'output/windows/LocalMessageHub-portable' }
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $OutputPath) { throw "Output already exists; choose a new path: $OutputPath" }

$pythonCommand = Get-Command $Python -ErrorAction Stop
$pythonVersion = & $pythonCommand.Source -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])'
if ($LASTEXITCODE -ne 0 -or $pythonVersion.Trim() -ne '312') {
    throw "Python 3.12 is required; found $pythonVersion"
}
$node = Get-Command npm.cmd -ErrorAction Stop
$ffmpegBinary = Join-Path $repo 'node_modules/ffmpeg-static/ffmpeg.exe'
if (-not (Test-Path -LiteralPath $ffmpegBinary -PathType Leaf)) {
    throw "Bundled ffmpeg-static binary is missing; install the locked root npm dependencies first: $ffmpegBinary"
}
foreach ($notice in @("$ffmpegBinary.LICENSE", "$ffmpegBinary.README")) {
    if (-not (Test-Path -LiteralPath $notice -PathType Leaf)) {
        throw "Bundled FFmpeg notice is missing: $notice"
    }
}

function Invoke-PackageSmokeTest([string]$PackageRoot) {
    $root = [System.IO.Path]::GetFullPath($PackageRoot)
    $exe = Join-Path $root 'LocalMessageHub.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Portable executable missing: $exe" }
    $ffmpeg = Join-Path $root '_internal/media/ffmpeg.exe'
    if (-not (Test-Path -LiteralPath $ffmpeg -PathType Leaf)) { throw 'Portable package omitted bundled FFmpeg.' }
    & $ffmpeg -version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Bundled FFmpeg did not run in the isolated package.' }
        $mcpExe = Join-Path $root 'mcp/LocalMessageHub-MCP.exe'
        if (-not (Test-Path -LiteralPath $mcpExe -PathType Leaf)) {
            throw "Standalone MCP executable missing: $mcpExe"
        }
    $smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-portable-smoke-' + [guid]::NewGuid().ToString('N'))
    $storage = Join-Path $smokeRoot 'isolated-storage'
    $stdout = Join-Path $smokeRoot 'server.stdout.log'
    $stderr = Join-Path $smokeRoot 'server.stderr.log'
    New-Item -ItemType Directory -Path $storage -Force | Out-Null
    if (@(Get-ChildItem -LiteralPath $storage -Force).Count -ne 0) { throw 'Portable smoke storage was not empty before startup.' }
    $arguments = '--port 0 --storage "{0}" --no-autosync --no-browser' -f $storage
    $smokeEnvironment = @{
        LOCALAPPDATA = $smokeRoot
        APPDATA = Join-Path $smokeRoot 'appdata'
        USERPROFILE = Join-Path $smokeRoot 'userprofile'
        LMH_STORAGE_DIR = $storage
        WECOM_LOCAL_DIR = Join-Path $storage 'integrations/wecom'
        WECHATAUTO_KEYS_DIR = Join-Path $storage 'integrations/wechat/keys'
        QCE_SECURITY_FILE = Join-Path $storage 'qq/security.json'
        QCE_BUNDLE_DIR = Join-Path $smokeRoot 'LocalMessageHub/runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64'
        QCE_BASE_URL = 'http://127.0.0.1:0'
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
                    if ($state.pid -eq $process.Id -and $state.url -match '^http://127\.0\.0\.1:(\d+)$' -and [int]$Matches[1] -gt 0) {
                        $url = $state.url
                        break
                    }
                } catch { }
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $url) { throw 'Portable Hub did not publish a loopback URL within 20 seconds.' }
        $page = Invoke-WebRequest -Uri "$url/" -UseBasicParsing -TimeoutSec 5
        if ($page.StatusCode -ne 200 -or $page.Content -notmatch '<html lang="zh-CN">') { throw 'Packaged WebUI was unavailable.' }
        $relationshipStyles = Invoke-WebRequest -Uri "$url/relationship.css" -UseBasicParsing -TimeoutSec 5
        if ($relationshipStyles.StatusCode -ne 200 -or $relationshipStyles.Content -notmatch '\.relationship-achievement-list') {
            throw 'Packaged relationship milestone styles were unavailable.'
        }
        $icons = Invoke-WebRequest -Uri "$url/vendor/lucide.js" -UseBasicParsing -TimeoutSec 5
        if ($icons.StatusCode -ne 200 -or $icons.Content.Length -lt 1000) { throw 'Packaged Lucide asset was unavailable.' }
        $sources = Invoke-RestMethod -Uri "$url/api/sources" -TimeoutSec 5
        if (@($sources.sources).Count -ne 3) { throw 'Expected three platform entries in the portable package.' }
        $settings = Invoke-RestMethod -Uri "$url/api/settings" -TimeoutSec 5
        if ([System.IO.Path]::GetFullPath($settings.storage_dir) -ne [System.IO.Path]::GetFullPath($storage)) {
            throw 'Portable executable did not use isolated temporary storage.'
        }
        $conversations = Invoke-RestMethod -Uri "$url/api/conversations?platform=all&limit=1" -TimeoutSec 5
        if ([int]$conversations.total -ne 0 -or @($conversations.items).Count -ne 0) {
            throw 'Portable smoke storage unexpectedly contained conversations.'
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
        foreach ($name in $smokeEnvironment.Keys) {
            [System.Environment]::SetEnvironmentVariable($name, $originalSmokeEnvironment[$name], 'Process')
        }
        if (Test-Path -LiteralPath $smokeRoot) { Remove-Item -LiteralPath $smokeRoot -Recurse -Force }
    }

    Invoke-StandaloneMcpSmokeTest -Executable $mcpExe -WorkingDirectory $root
}

function Invoke-StandaloneMcpSmokeTest([string]$Executable, [string]$WorkingDirectory) {
    $smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-mcp-smoke-' + [guid]::NewGuid().ToString('N'))
    $storage = Join-Path $smokeRoot 'isolated-storage'
    New-Item -ItemType Directory -Path $storage -Force | Out-Null
    $smokeEnvironment = @{
        LOCALAPPDATA = $smokeRoot
        APPDATA = Join-Path $smokeRoot 'appdata'
        USERPROFILE = Join-Path $smokeRoot 'userprofile'
        LMH_STORAGE_DIR = $storage
        WECOM_LOCAL_DIR = Join-Path $storage 'integrations/wecom'
        WECHATAUTO_KEYS_DIR = Join-Path $storage 'integrations/wechat/keys'
        QCE_SECURITY_FILE = Join-Path $storage 'qq/security.json'
        QCE_BUNDLE_DIR = Join-Path $smokeRoot 'LocalMessageHub/runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64'
        QCE_BASE_URL = 'http://127.0.0.1:0'
    }
    $process = $null
    try {
        $start = [System.Diagnostics.ProcessStartInfo]::new()
        $start.FileName = [System.IO.Path]::GetFullPath($Executable)
        $quotedStorage = '"' + $storage.Replace('"', '\\"') + '"'
        $start.Arguments = '--storage ' + $quotedStorage
        $start.WorkingDirectory = [System.IO.Path]::GetFullPath($WorkingDirectory)
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardInput = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        foreach ($name in $smokeEnvironment.Keys) { $start.Environment[$name] = $smokeEnvironment[$name] }
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $start
        if (-not $process.Start()) { throw 'Could not start standalone MCP executable.' }

        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $requests = @(
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"lmh-package-smoke","version":"1"}}}',
            '{"jsonrpc":"2.0","method":"notifications/initialized"}',
            '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}',
            '{"jsonrpc":"2.0","method":"exit"}'
        )
        foreach ($request in $requests) { $process.StandardInput.WriteLine($request) }
        $process.StandardInput.Close()

        if (-not $process.WaitForExit(15000)) {
            $process.Kill()
            $process.WaitForExit()
            throw 'Standalone MCP protocol smoke exceeded 15 seconds.'
        }
        $stdoutText = $stdoutTask.GetAwaiter().GetResult()
        $stderrText = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) { throw "Standalone MCP exited with code $($process.ExitCode). stderr=$stderrText" }

        $responses = @()
        foreach ($line in ($stdoutText -split "`r?`n" | Where-Object { $_ })) {
            try { $responses += ($line | ConvertFrom-Json -ErrorAction Stop) }
            catch { throw "Standalone MCP wrote non-JSON data to stdout: $line`nstderr=$stderrText" }
        }
        if (@($responses).Count -ne 2) { throw "Expected two MCP JSON-RPC responses; received $(@($responses).Count). stderr=$stderrText" }
        $initialize = @($responses | Where-Object { $_.id -eq 1 }) | Select-Object -First 1
        $toolList = @($responses | Where-Object { $_.id -eq 2 }) | Select-Object -First 1
        if (-not $initialize.result -or $initialize.result.serverInfo.name -ne 'local-message-hub') {
            throw "Standalone MCP initialize response was invalid. stderr=$stderrText"
        }
        if (-not $toolList.result.tools) { throw "Standalone MCP tools/list response was missing. stderr=$stderrText" }
        $toolNames = @($toolList.result.tools | ForEach-Object { $_.name })
        foreach ($expected in @('search_messages', 'list_conversations', 'get_attachment')) {
            if ($toolNames -notcontains $expected) { throw "Standalone MCP omitted expected tool '$expected'. stderr=$stderrText" }
        }
        if (-not (Test-Path -LiteralPath (Join-Path $storage 'hub.sqlite3') -PathType Leaf)) {
            throw 'Standalone MCP did not initialize its isolated storage database.'
        }
        Write-Output 'PASS: standalone MCP executable completed initialize/tools/list/exit over stdio with clean JSON-RPC stdout and isolated storage.'
    } finally {
        if ($process) { $process.Dispose() }
        if (Test-Path -LiteralPath $smokeRoot) { Remove-Item -LiteralPath $smokeRoot -Recurse -Force }
    }
}

function Assert-PortablePackageReadme([string]$PackageRoot, [bool]$IncludesQceRuntime) {
    $readmePath = Join-Path $PackageRoot 'README-PORTABLE.txt'
    if (-not (Test-Path -LiteralPath $readmePath -PathType Leaf)) {
        throw 'Portable package README is missing.'
    }
    $readme = Get-Content -LiteralPath $readmePath -Raw
    if ($readme -notmatch 'QQ history uses the local QQNT database reader') {
        throw 'Portable package README must describe local QQ history.'
    }
    if ($IncludesQceRuntime) {
        if ($readme -notmatch 'The optional NapCat/QCE runtime is included only for QQ OneBot sending' -or
            $readme -match 'This package omits NapCat/QCE') {
            throw 'Portable package README does not match the included NapCat/QCE runtime.'
        }
        foreach ($path in @('COPYING-QCE-GPL-3.0.txt', 'licenses/qq-chat-exporter-6.3.2-source/LICENSE')) {
            if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot $path) -PathType Leaf)) {
                throw "Portable package README claims QCE provenance without its notice/source: $path"
            }
        }
    } else {
        if ($readme -notmatch 'This package omits NapCat/QCE; QQ OneBot sending and QCE cache-gallery features are' -or
            $readme -match 'The optional NapCat/QCE runtime is included only') {
            throw 'Portable package README does not match the omitted NapCat/QCE runtime.'
        }
        if (Test-Path -LiteralPath (Join-Path $PackageRoot 'COPYING-QCE-GPL-3.0.txt')) {
            throw 'Portable package README says QCE is omitted but its QCE license notice is present.'
        }
    }
}

$temp = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-portable-build-' + [guid]::NewGuid().ToString('N'))
$stage = Join-Path $temp 'stage'
$venv = Join-Path $temp 'venv'
$dist = Join-Path $temp 'dist'
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$excludedNames = @('.git', '.local', '.runtime', 'runtime', '.cache', 'cache', 'caches',
                   'venv', '.venv', '__pycache__', 'node_modules', 'target', 'output', 'wecom-bot-outbox')
$excludedFiles = @('AGENTS.md', 'TASK.md', 'CLAUDE.md', 'CODEX.md',
                   'server.json', 'settings.json', 'wechat.json', 'wecom.json', 'security.json',
                   'onebot11.json', 'napcat.json', 'wecom-bot.credentials')
$excludedExtensions = @('.db', '.sqlite', '.sqlite3', '.db-wal', '.db-shm', '.sqlite-wal',
                        '.sqlite-shm', '.sqlite3-wal', '.sqlite3-shm', '.pyc', '.log', '.pem', '.key', '.blob')
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
    foreach ($relativePath in @('integrations/wecom/bot_credentials.py',
                                'integrations/wecom/bot_media.py',
                                'integrations/wecom/bot_sender.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $stage $relativePath) -PathType Leaf)) {
            throw "Portable package omitted required WeCom bot source: $relativePath"
        }
    }
    foreach ($file in @('integrations/qq/provider.py', 'integrations/qq/sender.py',
                        'integrations/qq/local_provider.py', 'integrations/qq/local_wal.py',
                        'integrations/qq/key_reader.py', 'integrations/qq/key_store.py',
                        'integrations/qq/send_target.py', 'integrations/qq/README.md',
                        'scripts/windows_portable.py', 'scripts/windows_mcp.py', 'start_hub.py')) {
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
    & $node.Source ci --prefix $webui --no-audit --no-fund
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
        $bootExe = Get-ChildItem -LiteralPath $unpack -Filter NapCatWinBootMain.exe -File -Recurse | Select-Object -First 1
        if (-not $bootExe) { throw 'Official QCE archive is missing the NapCat bootloader.' }
        $qceRoot = $bootExe.Directory.FullName
        if (-not (Test-Path -LiteralPath (Join-Path $qceRoot 'qce-server.exe') -PathType Leaf)) {
            throw 'Official QCE shell is missing its expected server placeholder.'
        }
        $runtimeTarget = Join-Path $stage '.runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64'
        New-Item -ItemType Directory -Path (Split-Path -Parent $runtimeTarget) -Force | Out-Null
        Copy-Item -LiteralPath $qceRoot -Destination $runtimeTarget -Recurse
        $qcePluginRuntime = Join-Path $runtimeTarget 'plugins/napcat-plugin-qce/runtime/rustBridge.mjs'
        $qcePluginSource = Join-Path $repo 'integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2/plugins/qq-chat-exporter/runtime/rustBridge.mjs'
        if (-not (Test-Path -LiteralPath $qcePluginRuntime -PathType Leaf) -or
            -not (Test-Path -LiteralPath $qcePluginSource -PathType Leaf)) {
            throw 'Bundled QCE plugin runtime or reviewed repository source is missing.'
        }
        Copy-Item -LiteralPath $qcePluginSource -Destination $qcePluginRuntime -Force
        if ($QceServerPath) {
            $qceServer = [System.IO.Path]::GetFullPath($QceServerPath)
            $qceHash = $prebuiltQceHash
        } else {
            $qceBuilder = Join-Path $repo 'scripts/build-qce-windows.ps1'
            if (-not (Test-Path -LiteralPath $qceBuilder -PathType Leaf)) { throw 'Vendored QCE Windows build script is missing.' }
            $qceServer = Join-Path $temp 'qce-server.exe'
            & $qceBuilder -OutputPath $qceServer
            if ($LASTEXITCODE -ne 0) { throw 'Building the vendored QCE server failed.' }
            $qceHash = (Get-Content -LiteralPath ($qceServer + '.sha256') -Raw).Trim().ToLowerInvariant()
            if ($qceHash -notmatch '^[0-9a-f]{64}$') { throw 'Vendored QCE build did not produce a valid SHA-256 digest.' }
            $actualQceHash = (Get-FileHash -LiteralPath $qceServer -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actualQceHash -ne $qceHash) { throw 'Vendored QCE sidecar SHA-256 does not match its executable.' }
        }
        Copy-Item -LiteralPath $qceServer -Destination (Join-Path $runtimeTarget 'qce-server.exe') -Force
        $providerPath = Join-Path $stage 'integrations/qq/provider.py'
        $providerText = Get-Content -LiteralPath $providerPath -Raw -Encoding UTF8
        $providerText = Add-ApprovedQceHash -ProviderText $providerText -Sha256 $qceHash
        Set-Content -LiteralPath $providerPath -Value $providerText -NoNewline -Encoding UTF8
        $licenseSource = Join-Path $repo 'integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2/LICENSE'
        if (-not (Test-Path -LiteralPath $licenseSource -PathType Leaf)) {
            throw 'QCE GPL-3.0 license text is missing from the verified upstream source tree.'
        }
        Copy-Item -LiteralPath $licenseSource -Destination (Join-Path $stage 'COPYING-QCE-GPL-3.0.txt')
        $sourceTarget = Join-Path $stage 'licenses/qq-chat-exporter-6.3.2-source'
        Copy-PublicTree (Join-Path $repo 'integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2') $sourceTarget -PreserveRuntimeTree
    }

    & (Join-Path $repo 'scripts/assert-package-privacy.ps1') -Root $stage
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Portable package privacy scan failed.' }

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
        '--hidden-import', 'hub.auto_export',
        '--hidden-import', 'hub.resources',
        '--hidden-import', 'integrations.qq.provider',
        '--hidden-import', 'integrations.qq.sender',
        '--hidden-import', 'integrations.qq.local_provider',
        '--hidden-import', 'integrations.qq.local_wal',
        '--hidden-import', 'integrations.qq.key_reader',
        '--hidden-import', 'integrations.qq.key_store',
        '--hidden-import', 'integrations.qq.send_target',
        '--hidden-import', 'integrations.telegram.adapter',
        '--collect-all', 'qrcode', '--collect-all', 'pilk_nogil', '--collect-binaries', 'Crypto',
        '--collect-all', 'ijson',
        '--add-binary', ($ffmpegBinary + ';media')
    )
    if (-not $OmitQceRuntime) { $common += @('--add-data', ((Join-Path $stage '.runtime') + ';.runtime')) }
    $previousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    $env:PYTHONDONTWRITEBYTECODE = '1'
    try {
        & $pyinstaller @common --name LocalMessageHub $entry
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed to build the Hub executable.' }
        & $pyinstaller @common --name LocalMessageHub-MCP $mcpEntry
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed to build the MCP executable.' }
    } finally {
        if ($null -eq $previousDontWriteBytecode) {
            Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONDONTWRITEBYTECODE = $previousDontWriteBytecode
        }
    }

    $payload = Join-Path $dist 'LocalMessageHub'
    if (-not (Test-Path -LiteralPath (Join-Path $payload '_internal/media/ffmpeg.exe') -PathType Leaf)) {
        throw 'Portable package omitted the bundled ffmpeg-static binary.'
    }
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
    $ffmpegLicenseDirectory = Join-Path $payload 'licenses/ffmpeg-static'
    New-Item -ItemType Directory -Path $ffmpegLicenseDirectory -Force | Out-Null
    Copy-Item -LiteralPath ($ffmpegBinary + '.LICENSE') -Destination $ffmpegLicenseDirectory
    Copy-Item -LiteralPath ($ffmpegBinary + '.README') -Destination $ffmpegLicenseDirectory
    Copy-Item -LiteralPath (Join-Path $repo 'node_modules/ffmpeg-static/LICENSE') `
        -Destination (Join-Path $ffmpegLicenseDirectory 'ffmpeg-static-package-LICENSE.txt')
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
    $qqRuntimeNote = if ($OmitQceRuntime) {
@'
QQ history uses the local QQNT database reader and does not start a QR login flow.
This package omits NapCat/QCE; QQ OneBot sending and QCE cache-gallery features are
unavailable. Key discovery is explicit, bounded, read-only, and saves a verified
key only in this user's private Hub storage.
'@
    } else {
@'
QQ history uses the local QQNT database reader and does not require QR login.
The optional NapCat/QCE runtime is included only for QQ OneBot sending and the QCE
cache gallery; it is copied to user-local data and never to QQ's install. Those
optional features can require an already-running, separately authenticated QQ.
See UPSTREAM.md and COPYING-QCE-GPL-3.0.txt for source provenance and notices.
'@
    }
    @"
Local Message Hub portable Windows build

Run START-LocalMessageHub.cmd to start the local WebUI service. It uses a bundled
Python runtime and bundled UI assets; starting the app does not run pip, npm, or
download software. Local Hub database and settings default to:
%LOCALAPPDATA%\LocalMessageHub

Run START-LocalMessageHub-MCP.cmd from an MCP client configuration to start the
standalone stdio server. Do not start it in a terminal that expects ordinary text
output; stdout is reserved for the MCP protocol.

$qqRuntimeNote

The portable runtime also includes the pinned ffmpeg-static Windows x64 binary
for offline WXGF/WXAM image decoding. Its upstream license and build/source
metadata are in licenses/ffmpeg-static/.

The portable build is unsigned and not an NSIS installer. Do not publish a build
without reviewing the included upstream licenses and applicable source-offer duties.
"@ | Set-Content -LiteralPath (Join-Path $payload 'README-PORTABLE.txt') -Encoding UTF8
    Assert-PortablePackageReadme -PackageRoot $payload -IncludesQceRuntime:(-not $OmitQceRuntime)

    if (-not $SkipSmokeTest) {
        Invoke-PackageSmokeTest $payload
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Portable package smoke test failed.' }
    }
    & (Join-Path $repo 'scripts/assert-package-privacy.ps1') -Root $payload
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Final portable package privacy scan failed.' }
    if (Test-Path -LiteralPath $OutputPath) { throw "Output appeared during the build; refusing to overwrite it: $OutputPath" }
    $outputParent = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    Move-Item -LiteralPath $payload -Destination $OutputPath
    Write-Output "Portable Windows build: $OutputPath"
    Write-Output 'Runtime startup does not invoke pip, npm, or a network installer.'
    if (-not $OmitQceRuntime) { Write-Output 'Included verified NapCat v6.3.2 shell and vendored-source QCE server.' }
    else { Write-Output 'QQ runtime deliberately omitted by -OmitQceRuntime.' }
} finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
