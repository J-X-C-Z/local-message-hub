param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$upstream = Join-Path $repo 'integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2'
$serverSource = Join-Path $upstream 'qq-chat-export-server'
$coreSource = Join-Path $upstream 'qq-chat-export-core'
$mainSource = Join-Path $serverSource 'src/main.rs'
if (-not (Select-String -LiteralPath $mainSource -Pattern '\[127,\s*0,\s*0,\s*1\]' -Quiet)) {
    throw 'Refusing to build QCE: the vendored server does not explicitly bind to IPv4 loopback.'
}
if (-not (Select-String -LiteralPath (Join-Path $serverSource 'src/api/routes/resources.rs') -Pattern 'dateFilterSupported' -Quiet)) {
    throw 'Refusing to build QCE: the vendored server is missing the required date-filter capability.'
}

$target = 'x86_64-pc-windows-gnu'
$pinnedToolchain = '1.98.1-x86_64-pc-windows-gnu'
$pinnedGccVersion = '16.2.0'
$toolchain = 'stable-x86_64-pc-windows-gnu'
$rustup = Get-Command rustup.exe -ErrorAction SilentlyContinue
if (-not $rustup) { $rustup = Get-Command rustup -ErrorAction SilentlyContinue }
if (-not $rustup) {
    $rustupPath = Join-Path $env:USERPROFILE '.cargo/bin/rustup.exe'
    if (Test-Path -LiteralPath $rustupPath) { $rustup = Get-Command -Name $rustupPath }
}
if ($rustup) {
    $installedToolchains = @(& $rustup.Source toolchain list)
    if ($installedToolchains -match '^1\.98\.1-x86_64-pc-windows-gnu') { $toolchain = $pinnedToolchain }
    elseif ($installedToolchains -notmatch '^stable-x86_64-pc-windows-gnu') { throw 'Install Rust 1.98.1 for the Windows GNU target.' }
    $cargo = (& $rustup.Source which --toolchain $toolchain cargo).Trim()
    $rustc = (& $rustup.Source which --toolchain $toolchain rustc).Trim()
} else {
    $toolchainRoot = Join-Path $env:USERPROFILE ".rustup/toolchains/$pinnedToolchain/bin"
    if (-not (Test-Path -LiteralPath (Join-Path $toolchainRoot 'cargo.exe'))) { $toolchainRoot = Join-Path $env:USERPROFILE '.rustup/toolchains/stable-x86_64-pc-windows-gnu/bin' }
    $cargo = Join-Path $toolchainRoot 'cargo.exe'
    $rustc = Join-Path $toolchainRoot 'rustc.exe'
}
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $cargo) -or -not (Test-Path -LiteralPath $rustc)) {
    throw "Rust GNU toolchain '$toolchain' is required to build bundled QCE."
}
$rustVersion = & $rustc --version
if ($LASTEXITCODE -ne 0 -or $rustVersion -notmatch '^rustc 1\.98\.1(?: |$)') {
    throw "Rust 1.98.1 is required to build bundled QCE; found $rustVersion."
}
$gcc = Get-Command gcc.exe -ErrorAction SilentlyContinue
if (-not $gcc) {
    $gccPath = 'C:\msys64\mingw64\bin\gcc.exe'
    if ($env:MSYS2_ROOT) { $gccPath = Join-Path $env:MSYS2_ROOT 'mingw64/bin/gcc.exe' }
    if (Test-Path -LiteralPath $gccPath) { $gcc = Get-Command -Name $gccPath }
}
if (-not $gcc) { throw 'MinGW-w64 gcc is required; add its MINGW64 bin directory to PATH or set MSYS2_ROOT.' }
$gccVersion = (& $gcc.Source --version | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or $gccVersion -notmatch ('\s' + [regex]::Escape($pinnedGccVersion) + '(?:\s|$)')) {
    throw "GCC $pinnedGccVersion is required to build bundled QCE; found $gccVersion."
}
Write-Output "Using QCE linker: $gccVersion"
$buildRoot = Join-Path $env:LOCALAPPDATA ('LocalMessageHub/qce-build-' + [guid]::NewGuid().ToString('N'))
if ($buildRoot -match '[^\x00-\x7F]') {
    throw 'QCE build path must contain ASCII characters because the Windows GNU toolchain cannot handle Unicode paths.'
}
$safeBuildParent = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'LocalMessageHub'))
if (-not $buildRoot.StartsWith($safeBuildParent + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to create a QCE build directory outside the LocalMessageHub temporary build folder.'
}
$stage = Join-Path $buildRoot 'source'
$targetDir = Join-Path $buildRoot 'target'
$server = Join-Path $stage 'qq-chat-export-server'
$core = Join-Path $stage 'qq-chat-export-core'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

try {
    foreach ($pair in @(@($serverSource, $server), @($coreSource, $core))) {
        $source = $pair[0]
        $destination = $pair[1]
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        foreach ($name in @('Cargo.toml', 'Cargo.lock', 'src', 'assets')) {
            $item = Join-Path $source $name
            if (Test-Path -LiteralPath $item) { Copy-Item -LiteralPath $item -Destination $destination -Recurse }
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $server 'Cargo.lock') -PathType Leaf)) {
        throw 'Vendored QCE Cargo.lock is missing; refusing an unlocked build.'
    }
    $oldPath = $env:PATH
    $oldRustc = $env:RUSTC
    $oldLinker = $env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER
    $oldTargetDir = $env:CARGO_TARGET_DIR
    try {
        $env:PATH = (Split-Path -Parent $gcc.Source) + ';' + (Split-Path -Parent $rustc) + ';' + $oldPath
        $env:RUSTC = $rustc
        $env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = $gcc.Source
        $env:CARGO_TARGET_DIR = $targetDir
        Push-Location $server
        try {
            & $cargo build --locked --release --target $target --manifest-path (Join-Path $server 'Cargo.toml')
            if ($LASTEXITCODE -ne 0) { throw 'Building vendored QCE server failed.' }
        } finally { Pop-Location }
    } finally {
        $env:PATH = $oldPath
        $env:RUSTC = $oldRustc
        $env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = $oldLinker
        $env:CARGO_TARGET_DIR = $oldTargetDir
    }

    $built = Join-Path $targetDir "$target/release/qce-server.exe"
    if (-not (Test-Path -LiteralPath $built -PathType Leaf)) { throw 'Cargo did not produce qce-server.exe.' }
    $output = [System.IO.Path]::GetFullPath($OutputPath)
    New-Item -ItemType Directory -Path (Split-Path -Parent $output) -Force | Out-Null
    Copy-Item -LiteralPath $built -Destination $output
    $hash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath ($output + '.sha256') -Value $hash -NoNewline -Encoding ASCII
    Write-Output "Built loopback QCE server: $output (SHA-256 $hash)"
} finally {
    $resolvedBuildRoot = [System.IO.Path]::GetFullPath($buildRoot)
    if ($resolvedBuildRoot.StartsWith($safeBuildParent + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedBuildRoot)) {
        Remove-Item -LiteralPath $resolvedBuildRoot -Recurse -Force
    }
}
