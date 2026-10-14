param(
    [Parameter(Mandatory = $true)]
    [string]$PortablePath,
    [Parameter(Mandatory = $true)]
    [string]$SourceBundlePath,
    [Parameter(Mandatory = $true)]
    [string]$CommitSha,
    [Parameter(Mandatory = $true)]
    [string]$WorkflowRunId,
    [ValidateSet('hosted-ci', 'local-worktree', 'synthetic-test')]
    [string]$BuildContext = 'local-worktree',
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
if ($CommitSha -notmatch '^[0-9a-fA-F]{40,64}$') { throw 'CommitSha must be a full Git object ID.' }
if ($WorkflowRunId -notmatch '^[A-Za-z0-9._-]{1,80}$') { throw 'WorkflowRunId contains unsupported characters.' }

$outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
$packages = @(
    @{ name = 'LocalMessageHub-portable'; path = [System.IO.Path]::GetFullPath($PortablePath); kind = 'directory' },
    @{ name = 'LocalMessageHub-source'; path = [System.IO.Path]::GetFullPath($SourceBundlePath); kind = 'file' }
)
foreach ($package in $packages) {
    $expectedType = if ($package.kind -eq 'directory') { 'Container' } else { 'Leaf' }
    if (-not (Test-Path -LiteralPath $package.path -PathType $expectedType)) {
        throw "Package input is missing or has the wrong type: $($package.name)"
    }
    $rootItem = Get-Item -LiteralPath $package.path -Force
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Package input cannot be a reparse point: $($package.name)"
    }
    if ($outputFullPath.StartsWith($package.path.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase) -or $outputFullPath -eq $package.path) {
        throw 'Manifest output must be outside package inputs.'
    }
}

$packageRecords = [System.Collections.Generic.List[object]]::new()
foreach ($package in $packages) {
    $files = if ($package.kind -eq 'directory') {
        $items = @(Get-ChildItem -LiteralPath $package.path -Recurse -Force)
        if (@($items | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 }).Count -gt 0) {
            throw "Package contains a reparse point: $($package.name)"
        }
        @($items | Where-Object { -not $_.PSIsContainer })
    } else {
        @((Get-Item -LiteralPath $package.path -Force))
    }
    if ($files.Count -eq 0) { throw "Package is empty: $($package.name)" }

    $entries = [System.Collections.Generic.List[object]]::new()
    foreach ($file in $files) {
        $relativePath = if ($package.kind -eq 'directory') {
            $file.FullName.Substring($package.path.TrimEnd('\', '/').Length + 1).Replace('\', '/')
        } else {
            [System.IO.Path]::GetFileName($file.FullName)
        }
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $entries.Add([ordered]@{ path = $relativePath; size = [long]$file.Length; sha256 = $hash })
    }
    $entryArray = $entries.ToArray()
    [Array]::Sort($entryArray, [System.Comparison[object]]{
        param($left, $right)
        [System.StringComparer]::Ordinal.Compare([string]$left.path, [string]$right.path)
    })
    $totalBytes = [long]0
    foreach ($entry in $entryArray) { $totalBytes += [long]$entry['size'] }
    $packageRecords.Add([ordered]@{
        name = $package.name
        kind = $package.kind
        file_count = $entryArray.Count
        total_bytes = $totalBytes
        files = $entryArray
    })
}

$manifest = [ordered]@{
    format_version = 1
    commit_sha = $CommitSha.ToLowerInvariant()
    workflow_run_id = $WorkflowRunId
    build_context = $BuildContext
    packages = $packageRecords.ToArray()
}
$json = ConvertTo-Json -InputObject $manifest -Depth 8
$parent = Split-Path -Parent $outputFullPath
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$stagedManifest = Join-Path $parent ('.' + [System.IO.Path]::GetFileName($outputFullPath) + '.' +
    [guid]::NewGuid().ToString('N') + '.tmp')
try {
    [System.IO.File]::WriteAllText($stagedManifest, $json + "`n", [System.Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $outputFullPath -PathType Leaf) {
        [System.IO.File]::Replace($stagedManifest, $outputFullPath, $null)
    } else {
        [System.IO.File]::Move($stagedManifest, $outputFullPath)
    }
} finally {
    if (Test-Path -LiteralPath $stagedManifest) {
        Remove-Item -LiteralPath $stagedManifest -Force
    }
}
Write-Output "Wrote package manifest for commit $($manifest.commit_sha), run $WorkflowRunId."
