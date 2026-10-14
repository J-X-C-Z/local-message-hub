$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$portableScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'build-windows-portable.ps1') -Raw
$portableAst = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PSScriptRoot 'build-windows-portable.ps1'), [ref]$null, [ref]$null)
$readmeAssertion = $portableAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Assert-PortablePackageReadme'
}, $true)
if (-not $readmeAssertion -or $portableScript -notmatch 'Assert-PortablePackageReadme -PackageRoot \$payload') {
    throw 'Portable build must validate its generated README against the selected QCE runtime mode.'
}
$prebuiltQceValidator = $portableAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-VerifiedPrebuiltQceHash'
}, $true)
$qceAllowlistAppender = $portableAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Add-ApprovedQceHash'
}, $true)
if (-not $prebuiltQceValidator -or
    -not $qceAllowlistAppender -or
    $portableScript -notmatch '\[string\]\$QceServerPath' -or
    $portableScript -notmatch '\[string\]\$QceServerSha256' -or
    $portableScript -notmatch '\$archiveHash -ne ''3fc093ba2332b97ac17e16160d5baa6ffe0a8def6c394d515a94777fe0ca492c''' -or
    $portableScript -notmatch '(?s)if \(\$QceServerPath\).*?\$qceHash = \$prebuiltQceHash.*?else \{.*?build-qce-windows\.ps1' -or
    $portableScript -notmatch '\$providerText = Add-ApprovedQceHash -ProviderText \$providerText -Sha256 \$qceHash') {
    throw 'Portable build must accept an independently hashed QCE exe, retain the pinned shell archive, and preserve source-build fallback.'
}
. ([scriptblock]::Create($readmeAssertion.Extent.Text))
. ([scriptblock]::Create($prebuiltQceValidator.Extent.Text))
. ([scriptblock]::Create($qceAllowlistAppender.Extent.Text))
$qceAllowlistAnchor = '"066d9ff15c3e6cb7efe23acc564b5b7b34d6da2849fec77a7dcc890290bb7745"'
$providerFixture = "APPROVED_QCE_HASHES = ($qceAllowlistAnchor,)"
$appendedProvider = Add-ApprovedQceHash -ProviderText $providerFixture -Sha256 ('A' * 64)
if ($appendedProvider -notmatch [regex]::Escape('"' + ('a' * 64) + '"') -or
    ([regex]::Matches($appendedProvider, [regex]::Escape($qceAllowlistAnchor))).Count -ne 1) {
    throw 'QCE allowlist appender did not add exactly one normalized digest beside the existing approved hash.'
}
foreach ($providerCase in @(
    @{ Text = 'APPROVED_QCE_HASHES = ()'; Hash = ('a' * 64) },
    @{ Text = "APPROVED_QCE_HASHES = ($qceAllowlistAnchor, $qceAllowlistAnchor,)"; Hash = ('a' * 64) },
    @{ Text = $providerFixture; Hash = 'not-a-sha256' }
)) {
    $rejected = $false
    try { Add-ApprovedQceHash -ProviderText $providerCase.Text -Sha256 $providerCase.Hash | Out-Null }
    catch { $rejected = $true }
    if (-not $rejected) { throw 'QCE allowlist appender accepted an invalid digest or ambiguous/missing anchor.' }
}
Write-Output 'PASS: QCE provider allowlist appends one normalized digest and rejects malformed or ambiguous inputs.'
$qceHashFixture = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-qce-hash-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $qceHashFixture | Out-Null
try {
    $qceFixtureExe = Join-Path $qceHashFixture 'qce-server.exe'
    [System.IO.File]::WriteAllBytes($qceFixtureExe, [byte[]](1, 2, 3, 4, 5))
    $validQceHash = (Get-FileHash -LiteralPath $qceFixtureExe -Algorithm SHA256).Hash
    if ((Get-VerifiedPrebuiltQceHash -Path $qceFixtureExe -Sha256 $validQceHash.ToUpperInvariant()) -cne $validQceHash.ToLowerInvariant()) {
        throw 'Prebuilt QCE validator did not normalize a verified uppercase digest.'
    }
    foreach ($case in @(
        @{ Path = $qceFixtureExe; Sha256 = '' },
        @{ Path = $qceFixtureExe; Sha256 = 'not-a-sha256' },
        @{ Path = $qceFixtureExe; Sha256 = ('0' * 64) },
        @{ Path = (Join-Path $qceHashFixture 'missing.exe'); Sha256 = $validQceHash }
    )) {
        $rejected = $false
        try { Get-VerifiedPrebuiltQceHash -Path $case.Path -Sha256 $case.Sha256 | Out-Null }
        catch { $rejected = $true }
        if (-not $rejected) { throw 'Prebuilt QCE validator accepted a missing, malformed, or mismatched digest input.' }
    }
    if ((Get-VerifiedPrebuiltQceHash -Path '' -Sha256 '') -cne '') {
        throw 'Prebuilt QCE validator must preserve the default source-build mode when no override is supplied.'
    }
    Write-Output 'PASS: prebuilt QCE requires a matching SHA-256, rejects malformed inputs, and leaves source-build default intact.'
} finally {
    if (Test-Path -LiteralPath $qceHashFixture) { Remove-Item -LiteralPath $qceHashFixture -Recurse -Force }
}
$readmeFixture = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-readme-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $readmeFixture | Out-Null
try {
    $qceFixture = Join-Path $readmeFixture 'qce'
    New-Item -ItemType Directory -Path (Join-Path $qceFixture 'licenses/qq-chat-exporter-6.3.2-source') -Force | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $qceFixture 'COPYING-QCE-GPL-3.0.txt'), 'fixture')
    [System.IO.File]::WriteAllText((Join-Path $qceFixture 'licenses/qq-chat-exporter-6.3.2-source/LICENSE'), 'fixture')
    [System.IO.File]::WriteAllText((Join-Path $qceFixture 'README-PORTABLE.txt'), @'
QQ history uses the local QQNT database reader.
The optional NapCat/QCE runtime is included only for QQ OneBot sending.
'@)
    Assert-PortablePackageReadme -PackageRoot $qceFixture -IncludesQceRuntime $true
    Remove-Item -LiteralPath (Join-Path $qceFixture 'COPYING-QCE-GPL-3.0.txt')
    Remove-Item -LiteralPath (Join-Path $qceFixture 'licenses/qq-chat-exporter-6.3.2-source/LICENSE')
    [System.IO.File]::WriteAllText((Join-Path $qceFixture 'README-PORTABLE.txt'), @'
QQ history uses the local QQNT database reader.
This package omits NapCat/QCE; QQ OneBot sending and QCE cache-gallery features are unavailable.
'@)
    Assert-PortablePackageReadme -PackageRoot $qceFixture -IncludesQceRuntime $false
    [System.IO.File]::WriteAllText((Join-Path $qceFixture 'README-PORTABLE.txt'), @'
QQ history uses the local QQNT database reader.
This package omits NapCat/QCE; QQ OneBot sending and QCE cache-gallery features are unavailable.
The optional NapCat/QCE runtime is included only for QQ OneBot sending.
'@)
    $mismatchRejected = $false
    try { Assert-PortablePackageReadme -PackageRoot $qceFixture -IncludesQceRuntime $false }
    catch { $mismatchRejected = $true }
    if (-not $mismatchRejected) { throw 'Portable README assertion accepted contradictory QCE runtime notes.' }
    Write-Output 'PASS: portable README assertion validates included, omitted, and contradictory QCE runtime notes.'
} finally {
    if (Test-Path -LiteralPath $readmeFixture) { Remove-Item -LiteralPath $readmeFixture -Recurse -Force }
}
$sourceScript = Join-Path $PSScriptRoot 'package-windows-source.ps1'
$sourceText = Get-Content -LiteralPath $sourceScript -Raw
$sourceAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $sourceScript, [ref]$null, [ref]$null)
$sourceTreeRootGuard = $sourceAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Assert-PackageSourceTreeRoot'
}, $true)
if (-not $sourceTreeRootGuard -or
    $sourceText -notmatch '(?s)function Copy-SourceTree.*?Assert-PackageSourceTreeRoot -SourcePath \$SourcePath') {
    throw 'Source packaging must validate explicitly selected tree roots before traversing them.'
}
. ([scriptblock]::Create($sourceTreeRootGuard.Extent.Text))
$sourceRootFixture = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-source-root-test-' + [guid]::NewGuid().ToString('N'))
$sourceRootOutside = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-source-root-target-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $sourceRootFixture,$sourceRootOutside | Out-Null
try {
    $normalRoot = Join-Path $sourceRootFixture 'normal'
    $linkedRoot = Join-Path $sourceRootFixture 'linked'
    New-Item -ItemType Directory -Path $normalRoot | Out-Null
    New-Item -ItemType Junction -Path $linkedRoot -Target $sourceRootOutside | Out-Null
    Assert-PackageSourceTreeRoot -SourcePath $normalRoot
    $linkedRootRejected = $false
    try { Assert-PackageSourceTreeRoot -SourcePath $linkedRoot }
    catch { $linkedRootRejected = $true }
    if (-not $linkedRootRejected) { throw 'Source packager accepted an explicit junction as a traversal root.' }
    Write-Output 'PASS: source package traversal accepts real directories and rejects explicit junction roots.'
} finally {
    if (Test-Path -LiteralPath $sourceRootFixture) { Remove-Item -LiteralPath $sourceRootFixture -Recurse -Force }
    if (Test-Path -LiteralPath $sourceRootOutside) { Remove-Item -LiteralPath $sourceRootOutside -Recurse -Force }
}
$qceBuildText = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'build-qce-windows.ps1') -Raw
if ($portableScript -notmatch "'--collect-binaries', 'Crypto'" -or
    $portableScript -match "'--collect-all', 'Crypto'") {
    throw 'Portable package must include Crypto native binaries without its development test-key fixtures.'
}
foreach ($qqModule in @('local_provider', 'local_wal', 'key_reader', 'key_store', 'send_target')) {
    if ($portableScript -notmatch [regex]::Escape("integrations/qq/$qqModule.py") -or
        $portableScript -notmatch [regex]::Escape("integrations.qq.$qqModule")) {
        throw "Portable package must stage and import the QQ $qqModule module."
    }
    if ($sourceText -notmatch [regex]::Escape("integrations/qq/$qqModule.py")) {
        throw "Source package must include the QQ $qqModule module."
    }
}
if ($portableScript -notmatch 'build-qce-windows\.ps1' -or $portableScript -notmatch 'Add-ApprovedQceHash' -or
    $portableScript -notmatch 'Add-ApprovedQceHash -ProviderText \$providerText -Sha256 \$qceHash') {
    throw 'Portable package must build vendored QCE and embed its digest in the provider allowlist.'
}
if ($portableScript -notmatch '\$qqRuntimeNote = if \(\$OmitQceRuntime\)' -or
    $portableScript -notmatch 'QQ history uses the local QQNT database reader and does not start a QR login flow\.' -or
    $portableScript -notmatch 'This package omits NapCat/QCE; QQ OneBot sending and QCE cache-gallery features are') {
    throw 'Portable package notes must describe local QQ history and accurately disclose omitted QR/NapCat features.'
}
if ($qceBuildText -notmatch 'cargo build --locked' -or $qceBuildText -notmatch '127,\\s\*0') {
    throw 'Vendored QCE build must use its lockfile and require an explicit loopback bind.'
}
$workflowText = Get-Content -LiteralPath (Join-Path $repo '.github/workflows/local-hub-ci.yml') -Raw
$ffmpegInstallStep = [regex]::Match($workflowText, '(?s)- name: Install locked root dependencies for the portable runtime.*?(?=\r?\n      - name: )').Value
$portableBuildStep = $workflowText.IndexOf('Build and smoke-test Windows portable package', [System.StringComparison]::Ordinal)
if (-not $ffmpegInstallStep -or
    $ffmpegInstallStep -notmatch 'npm ci --ignore-scripts --legacy-peer-deps --no-audit --no-fund' -or
    $ffmpegInstallStep -notmatch 'node node_modules/ffmpeg-static/install\.js' -or
    $ffmpegInstallStep -notmatch 'node_modules/ffmpeg-static/ffmpeg\.exe' -or
    $portableBuildStep -lt 0 -or
    $workflowText.IndexOf($ffmpegInstallStep, [System.StringComparison]::Ordinal) -gt $portableBuildStep) {
    throw 'Windows CI must install lockfile-pinned root dependencies and the ffmpeg-static binary before packaging.'
}
if ($qceBuildText -notmatch "`\$pinnedGccVersion = '16\.2\.0'" -or
    $qceBuildText -notmatch '\$gcc\.Source --version' -or
    $workflowText -notmatch '(?s)msys2/setup-msys2@v2.*?update: true' -or
    $workflowText -notmatch 'steps\.msys2\.outputs\.msys2-location' -or
    $workflowText -notmatch 'pacman -S --noconfirm --needed mingw-w64-x86_64-gcc' -or
    $workflowText -notmatch 'Expected GCC 16\.2\.0' -or
    $workflowText -notmatch 'gcc --version') {
    throw 'Vendored QCE CI must install GCC from the updated MSYS2 repositories and verify the compiler version.'
}
foreach ($scriptText in @($portableScript, $sourceText)) {
    if ($scriptText -notmatch "--port 0") { throw 'Package smoke must request an OS-assigned port.' }
    if ($scriptText -notmatch 'storage was not empty before startup') { throw 'Package smoke must reject non-empty initial storage.' }
    if ($scriptText -notmatch '/api/conversations\?platform=all&limit=1') { throw 'Package smoke must query the isolated conversation list.' }
    foreach ($name in @('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'LMH_STORAGE_DIR', 'WECOM_LOCAL_DIR',
                        'WECHATAUTO_KEYS_DIR', 'QCE_SECURITY_FILE', 'QCE_BUNDLE_DIR', 'QCE_BASE_URL')) {
        if ($scriptText -notmatch [regex]::Escape("$name =")) { throw "Package smoke must isolate $name." }
    }
}
if ($portableScript -notmatch 'state\.pid -eq \$process\.Id') {
    throw 'Portable executable smoke must verify the bundled server process identity.'
}
if ($sourceText -notmatch '\[long\]\$state\.pid -gt 0' -or
    $sourceText -notmatch 'http://127') {
    throw 'Source smoke must verify a valid server process and a loopback-only URL.'
}
$standaloneMcpSmoke = [regex]::Match($portableScript, '(?s)function Invoke-StandaloneMcpSmokeTest.*?(?=\r?\n})').Value
foreach ($name in @('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'LMH_STORAGE_DIR', 'WECOM_LOCAL_DIR',
                    'WECHATAUTO_KEYS_DIR', 'QCE_SECURITY_FILE', 'QCE_BUNDLE_DIR', 'QCE_BASE_URL')) {
    if ($standaloneMcpSmoke -notmatch [regex]::Escape("$name =")) {
        throw "Standalone MCP package smoke must isolate $name."
    }
}
if ($standaloneMcpSmoke -notmatch '\$start\.Environment\[\$name\] = \$smokeEnvironment\[\$name\]') {
    throw 'Standalone MCP package smoke must pass its isolated environment to the child process.'
}
$smokePosition = $portableScript.IndexOf('Invoke-PackageSmokeTest $payload', [System.StringComparison]::Ordinal)
$finalPrivacyPosition = $portableScript.LastIndexOf("assert-package-privacy.ps1') -Root `$payload", [System.StringComparison]::Ordinal)
if ($smokePosition -lt 0 -or $finalPrivacyPosition -lt $smokePosition) {
    throw 'Portable package privacy must be rescanned after the packaged smoke test.'
}

$fixtureRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-smoke-fixture-' + [guid]::NewGuid().ToString('N'))
$packageRoot = Join-Path $fixtureRoot 'package'
$hubRoot = Join-Path $packageRoot 'hub'
New-Item -ItemType Directory -Path $hubRoot -Force | Out-Null
Set-Content -LiteralPath (Join-Path $hubRoot '__init__.py') -Value '' -Encoding UTF8
$server = @'
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--storage', required=True)
parser.add_argument('--no-autosync', action='store_true')
args = parser.parse_args()
storage = Path(args.storage).resolve()
storage.mkdir(parents=True, exist_ok=True)
local_app_data = Path(os.environ['LOCALAPPDATA'])
app_data = Path(os.environ['APPDATA'])
user_profile = Path(os.environ['USERPROFILE'])
(local_app_data / 'LocalMessageHub').mkdir(parents=True, exist_ok=True)
(app_data / 'Tencent' / 'xwechat' / 'config').mkdir(parents=True, exist_ok=True)
(user_profile / '.qq-chat-exporter').mkdir(parents=True, exist_ok=True)
captured = {name: os.environ.get(name) for name in (
    'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'LMH_STORAGE_DIR', 'WECOM_LOCAL_DIR',
    'WECHATAUTO_KEYS_DIR', 'QCE_SECURITY_FILE', 'QCE_BUNDLE_DIR', 'QCE_BASE_URL',
    'PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'VIRTUAL_ENV', 'PYTHONNOUSERSITE')}
Path(os.environ['LMH_FIXTURE_ENV_CAPTURE']).write_text(json.dumps(captured))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            body = b'<html lang="zh-CN"></html>'
            content_type = 'text/html'
        elif path == '/api/sources':
            body = json.dumps({'sources': [{}, {}, {}]}).encode()
            content_type = 'application/json'
        elif path == '/api/settings':
            reported = str(storage / 'wrong') if os.environ.get('LMH_FIXTURE_WRONG_STORAGE') else str(storage)
            body = json.dumps({'storage_dir': reported}).encode()
            content_type = 'application/json'
        elif path == '/api/conversations':
            count = int(os.environ.get('LMH_FIXTURE_CONVERSATIONS', '0'))
            items = [{'id': 'synthetic-conversation'}] if count else []
            body = json.dumps({'items': items, 'total': count}).encode()
            content_type = 'application/json'
        elif path == '/api/mcp':
            body = json.dumps({'available': True, 'mode': 'read-only'}).encode()
            content_type = 'application/json'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.end_headers()
        self.wfile.write(body)

server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
(storage / 'server.json').write_text(json.dumps({'pid': os.getpid(), 'url': f'http://127.0.0.1:{server.server_port}'}))
server.serve_forever()
'@
Set-Content -LiteralPath (Join-Path $hubRoot 'server.py') -Value $server -Encoding UTF8
$oldConversationSetting = $env:LMH_FIXTURE_CONVERSATIONS
$oldStorageSetting = $env:LMH_FIXTURE_WRONG_STORAGE
$envNames = @('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'LMH_STORAGE_DIR', 'WECOM_LOCAL_DIR',
              'WECHATAUTO_KEYS_DIR', 'QCE_SECURITY_FILE', 'QCE_BUNDLE_DIR', 'QCE_BASE_URL',
              'PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'VIRTUAL_ENV', 'PYTHONNOUSERSITE',
              'LMH_FIXTURE_ENV_CAPTURE')
$oldEnvironment = @{}
foreach ($name in $envNames) { $oldEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, 'Process') }
$sentinelRoot = Join-Path $fixtureRoot 'user-config-sentinel'
$sentinelEnvironment = @{
    LOCALAPPDATA = Join-Path $sentinelRoot 'local'
    APPDATA = Join-Path $sentinelRoot 'roaming'
    USERPROFILE = Join-Path $sentinelRoot 'profile'
    LMH_STORAGE_DIR = Join-Path $sentinelRoot 'hub'
    WECOM_LOCAL_DIR = Join-Path $sentinelRoot 'wecom'
    WECHATAUTO_KEYS_DIR = Join-Path $sentinelRoot 'wechat-keys'
    QCE_SECURITY_FILE = Join-Path $sentinelRoot 'qq/security.json'
    QCE_BUNDLE_DIR = Join-Path $sentinelRoot 'qq/bundle'
    QCE_BASE_URL = 'http://127.0.0.1:43210'
    PYTHONPATH = Join-Path $sentinelRoot 'pythonpath'
    PYTHONHOME = Join-Path $sentinelRoot 'pythonhome'
    PYTHONUSERBASE = Join-Path $sentinelRoot 'python-user-base'
    VIRTUAL_ENV = Join-Path $sentinelRoot 'venv'
    PYTHONNOUSERSITE = '0'
    LMH_FIXTURE_ENV_CAPTURE = Join-Path $fixtureRoot 'child-environment.json'
}
$occupiedPortListener = $null
try {
    foreach ($name in $sentinelEnvironment.Keys) {
        [System.Environment]::SetEnvironmentVariable($name, $sentinelEnvironment[$name], 'Process')
    }
    $occupiedPortListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $occupiedPortListener.Start()
    $occupiedPort = $occupiedPortListener.LocalEndpoint.Port
    & $sourceScript -SmokePackageRoot $packageRoot
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Smoke failed while another loopback port was occupied.' }
    $capturedEnvironment = Get-Content -LiteralPath $sentinelEnvironment.LMH_FIXTURE_ENV_CAPTURE -Raw | ConvertFrom-Json
    $isolatedRoot = [System.IO.Path]::GetFullPath($capturedEnvironment.LOCALAPPDATA).TrimEnd('\') + '\'
    foreach ($name in @('APPDATA', 'USERPROFILE', 'LMH_STORAGE_DIR', 'WECOM_LOCAL_DIR', 'WECHATAUTO_KEYS_DIR',
                        'QCE_SECURITY_FILE', 'QCE_BUNDLE_DIR')) {
        $path = [System.IO.Path]::GetFullPath($capturedEnvironment.$name)
        if (-not $path.StartsWith($isolatedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Smoke child environment $name escaped its temporary LOCALAPPDATA root."
        }
    }
    if ($capturedEnvironment.QCE_BASE_URL -ne 'http://127.0.0.1:0') { throw 'Smoke retained a configured QCE endpoint.' }
    foreach ($name in @('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'VIRTUAL_ENV')) {
        if (-not [string]::IsNullOrWhiteSpace([string]$capturedEnvironment.$name)) {
            throw "Smoke child inherited Python environment override $name."
        }
    }
    if ($capturedEnvironment.PYTHONNOUSERSITE -ne '1') { throw 'Smoke child did not disable Python user site packages.' }
    foreach ($name in $envNames) {
        if ([System.Environment]::GetEnvironmentVariable($name, 'Process') -ne $sentinelEnvironment[$name]) {
            throw "Smoke did not restore the parent environment variable $name."
        }
    }
    if (Test-Path -LiteralPath $sentinelRoot) { throw 'Synthetic user configuration sentinel was touched by smoke.' }
    Write-Output 'PASS: smoke child app/config paths stayed temporary and the parent environment was restored.'

    $env:LMH_FIXTURE_WRONG_STORAGE = '1'
    $storageFailure = $false
    try { & $sourceScript -SmokePackageRoot $packageRoot }
    catch { $storageFailure = $_.Exception.Message -like '*isolated temporary storage directory*' }
    if (-not $storageFailure) { throw 'Smoke accepted a server configured with the wrong storage path.' }

    $env:LMH_FIXTURE_WRONG_STORAGE = $null
    $env:LMH_FIXTURE_CONVERSATIONS = '1'
    $conversationFailure = $false
    try { & $sourceScript -SmokePackageRoot $packageRoot }
    catch { $conversationFailure = $_.Exception.Message -like '*unexpectedly contained conversations*' }
    if (-not $conversationFailure) { throw 'Smoke accepted a non-empty synthetic conversation list.' }

    $firstArchive = Join-Path $fixtureRoot 'source-first.zip'
    $secondArchive = Join-Path $fixtureRoot 'source-second.zip'
    & $sourceScript -OutputPath $firstArchive -SkipSmokeTest | Out-Null
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'First deterministic source package build failed.' }
    & $sourceScript -OutputPath $secondArchive -SkipSmokeTest | Out-Null
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Second deterministic source package build failed.' }
    $firstHash = (Get-FileHash -LiteralPath $firstArchive -Algorithm SHA256).Hash
    $secondHash = (Get-FileHash -LiteralPath $secondArchive -Algorithm SHA256).Hash
    if ($firstHash -cne $secondHash) { throw 'Source package bytes changed across identical builds.' }

    $archive = [System.IO.Compression.ZipFile]::OpenRead($firstArchive)
    try {
        $seenEntries = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        $entryNames = [System.Collections.Generic.List[string]]::new()
        $fixedTimestamp = [DateTime]::new(1980, 1, 1, 0, 0, 0)
        foreach ($entry in $archive.Entries) {
            $name = $entry.FullName
            $segments = $name -split '/'
            if ([string]::IsNullOrWhiteSpace($name) -or $name.Contains('\\') -or $name.StartsWith('/') -or
                $name -match '^[A-Za-z]:' -or @($segments | Where-Object { $_ -in @('', '.', '..') }).Count -gt 0) {
                throw "Source package contains a non-canonical or unsafe ZIP path: $name"
            }
            if (-not $seenEntries.Add($name)) { throw "Source package contains a duplicate Windows path: $name" }
            if ($entry.LastWriteTime.DateTime -ne $fixedTimestamp) { throw "Source package entry timestamp is not fixed: $name" }
            $entryNames.Add($name)
        }
        $sortedEntryNames = [string[]]$entryNames.ToArray()
        [Array]::Sort($sortedEntryNames, [System.StringComparer]::Ordinal)
        for ($index = 0; $index -lt $sortedEntryNames.Length; $index++) {
            if ($entryNames[$index] -cne $sortedEntryNames[$index]) {
                throw 'Source package entries are not in ordinal path order.'
            }
        }
    } finally {
        $archive.Dispose()
    }

    Write-Output "PASS: source smoke uses an OS-assigned port despite occupied loopback port $occupiedPort."
    Write-Output 'PASS: wrong storage and non-empty synthetic conversations are rejected.'
    Write-Output 'PASS: source ZIP bytes, entry order, timestamps, and Windows-safe paths are deterministic.'
} finally {
    if ($occupiedPortListener) { $occupiedPortListener.Stop() }
    $env:LMH_FIXTURE_CONVERSATIONS = $oldConversationSetting
    $env:LMH_FIXTURE_WRONG_STORAGE = $oldStorageSetting
    foreach ($name in $envNames) {
        [System.Environment]::SetEnvironmentVariable($name, $oldEnvironment[$name], 'Process')
    }
    if (Test-Path -LiteralPath $fixtureRoot) { Remove-Item -LiteralPath $fixtureRoot -Recurse -Force }
}
