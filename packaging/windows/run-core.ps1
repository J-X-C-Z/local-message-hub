# Run from PowerShell 7.3+; keep the process in the foreground.
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Snapshot', 'Live')]
    [string]$Mode,
    [Parameter(Mandatory)]
    [string]$Archive,
    [Parameter(Mandatory)]
    [string]$Grants,
    [ValidateRange(0, 65535)]
    [int]$Port = 0
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or $PSVersionTable.PSVersion -lt [version]'7.3' -or
    [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne
        [System.Runtime.InteropServices.Architecture]::X64) {
    throw 'This bundle requires Windows x64 and PowerShell 7.3 or later.'
}

function Resolve-LocalItem([string]$Path, [bool]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSProvider.Name -ne 'FileSystem' -or $item.PSIsContainer -ne $Directory -or
        $item.Attributes.HasFlag([System.IO.FileAttributes]::ReparsePoint) -or
        $item.FullName.StartsWith('\\')) {
        throw 'Use a regular file or directory on a trusted local filesystem (no reparse points).'
    }
    return $item.FullName
}

$exe = Resolve-LocalItem (Join-Path $PSScriptRoot 'bin/local-message-hub-rs.exe') $false
$archivePath = Resolve-LocalItem $Archive ($Mode -eq 'Live')
$grantPath = Resolve-LocalItem $Grants $false
$archiveFlag = if ($Mode -eq 'Snapshot') { '--snapshot' } else { '--live' }

# Standard PowerShell 7.3+ argument passing preserves native argv (including quotes/spaces).
$PSNativeCommandArgumentPassing = 'Standard'
$nativeArgs = @('serve', $archiveFlag, $archivePath, '--grants', $grantPath, '--port', "$Port")
& $exe @nativeArgs
exit $LASTEXITCODE
