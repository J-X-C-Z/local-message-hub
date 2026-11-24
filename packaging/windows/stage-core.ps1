# Stage a Windows x64 portable folder from an operator-supplied release binary.
# No build, install, registry writes, or archive creation.
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)]
    [string]$Binary,
    [Parameter(Mandatory)]
    [string]$Destination
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or $PSVersionTable.PSVersion -lt [version]'7.3') {
    throw 'Stage this bundle on Windows with PowerShell 7.3 or later.'
}

$source = Get-Item -LiteralPath $Binary -Force
if ($source.PSProvider.Name -ne 'FileSystem' -or $source.PSIsContainer -or
    $source.Attributes.HasFlag([System.IO.FileAttributes]::ReparsePoint) -or
    $source.FullName.StartsWith('\\')) {
    throw 'Binary must be a regular local file.'
}
# Reject non-PE and wrong-architecture inputs before copying; this is not signature verification.
$stream = [System.IO.File]::OpenRead($source.FullName)
$reader = [System.IO.BinaryReader]::new($stream)
try {
    if ($stream.Length -lt 64 -or $reader.ReadUInt16() -ne 0x5a4d) {
        throw 'Expected a Windows x64 executable (PE32+).'
    }
    $stream.Position = 60
    $peStart = $reader.ReadInt32()
    if ($peStart -lt 64 -or ([long]$peStart + 26) -gt $stream.Length) {
        throw 'Invalid PE header offset.'
    }
    $stream.Position = $peStart
    if ($reader.ReadUInt32() -ne 0x4550 -or $reader.ReadUInt16() -ne 0x8664) {
        throw 'Wrong binary format or architecture; use x86_64-pc-windows-msvc.'
    }
    $stream.Position = $peStart + 20
    $optionalSize = $reader.ReadUInt16()
    $flags = $reader.ReadUInt16()
    if ($optionalSize -lt 2 -or ([long]$peStart + 24 + $optionalSize) -gt $stream.Length -or
        ($flags -band 0x2002) -ne 0x0002 -or $reader.ReadUInt16() -ne 0x20b) {
        throw 'Expected a Windows x64 executable (PE32+, not a DLL).'
    }
} finally {
    $reader.Dispose()
}

$fullDestination = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination)
$parent = [System.IO.Path]::GetDirectoryName($fullDestination)
if (-not $parent -or -not [System.IO.Directory]::Exists($parent) -or
    $fullDestination.StartsWith('\\') -or
    [System.IO.File]::Exists($fullDestination) -or [System.IO.Directory]::Exists($fullDestination)) {
    throw 'Use a new folder under an existing, trusted local parent directory.'
}
# Do not replace a prior staging folder.
$null = New-Item -ItemType Directory -Path $fullDestination
try {
    $bin = [System.IO.Directory]::CreateDirectory((Join-Path $fullDestination 'bin'))
    [System.IO.File]::Copy($source.FullName, (Join-Path $bin.FullName 'local-message-hub-rs.exe'))
    [System.IO.File]::Copy((Join-Path $PSScriptRoot 'run-core.ps1'), (Join-Path $fullDestination 'run-core.ps1'))
    [System.IO.File]::Copy((Join-Path $PSScriptRoot '../README.md'), (Join-Path $fullDestination 'README.md'))
} catch {
    # Leave incomplete contents for operator inspection; never delete a supplied path.
    throw 'Staging failed; do not distribute this partial folder. Retry with a new destination.'
}
