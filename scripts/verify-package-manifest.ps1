param(
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$PortablePath,
    [Parameter(Mandatory = $true)][string]$SourceBundlePath,
    [Parameter(Mandatory = $true)][string]$CommitSha,
    [Parameter(Mandatory = $true)][string]$WorkflowRunId,
    [ValidateSet('hosted-ci', 'local-worktree', 'synthetic-test')][string]$BuildContext = 'hosted-ci'
)

$ErrorActionPreference = 'Stop'
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($manifest.format_version -ne 1 -or
    $manifest.commit_sha -ne $CommitSha.ToLowerInvariant() -or
    $manifest.workflow_run_id -ne $WorkflowRunId -or
    $manifest.build_context -ne $BuildContext) {
    throw 'Artifact manifest provenance does not match the expected build.'
}

$expectedPackages = @(
    @{ name = 'LocalMessageHub-portable'; kind = 'directory'; path = [System.IO.Path]::GetFullPath($PortablePath) },
    @{ name = 'LocalMessageHub-source'; kind = 'file'; path = [System.IO.Path]::GetFullPath($SourceBundlePath) }
)
if ($manifest.packages.Count -ne $expectedPackages.Count) { throw 'Artifact manifest package count is incorrect.' }

for ($packageIndex = 0; $packageIndex -lt $expectedPackages.Count; $packageIndex++) {
    $expected = $expectedPackages[$packageIndex]
    $record = $manifest.packages[$packageIndex]
    if ($record.name -ne $expected.name -or $record.kind -ne $expected.kind) {
        throw "Artifact manifest package identity mismatch: $($expected.name)"
    }
    $rootItem = Get-Item -LiteralPath $expected.path -Force
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Package input cannot be a reparse point: $($expected.name)"
    }

    if ($expected.kind -eq 'directory') {
        $items = @(Get-ChildItem -LiteralPath $expected.path -Recurse -Force)
        if (@($items | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 }).Count -gt 0) {
            throw "Package contains a reparse point: $($expected.name)"
        }
        $actualFiles = @($items | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
            [pscustomobject]@{
                path = $_.FullName.Substring($expected.path.TrimEnd('\', '/').Length + 1).Replace('\', '/')
                file = $_
            }
        })
    } else {
        $file = Get-Item -LiteralPath $expected.path -Force
        if (($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Package input cannot be a reparse point: $($expected.name)"
        }
        $actualFiles = @([pscustomobject]@{ path = $file.Name; file = $file })
    }
    if ($actualFiles.Count -ne $record.file_count -or $record.files.Count -ne $record.file_count) {
        throw "Artifact manifest file count mismatch: $($expected.name) (actual=$($actualFiles.Count), recorded=$($record.file_count), entries=$($record.files.Count))"
    }

    $actualByPath = [System.Collections.Generic.Dictionary[string, object]]::new([System.StringComparer]::Ordinal)
    foreach ($actual in $actualFiles) {
        $relativePath = [string]$actual.path
        if ($actualByPath.ContainsKey($relativePath)) {
            throw "Duplicate package path: $($actual.path)"
        }
        $actualByPath.Add($relativePath, $actual.file)
    }
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    $totalBytes = [long]0
    foreach ($entry in $record.files) {
        $relativePath = [string]$entry.path
        if ([string]::IsNullOrWhiteSpace($relativePath) -or $relativePath.StartsWith('/') -or
            $relativePath -match '(^|/)\.\.?(/|$)' -or $relativePath.Contains('\') -or
            -not $seen.Add($relativePath)) {
            throw "Unsafe or duplicate manifest path in $($expected.name)."
        }
        if (-not $actualByPath.ContainsKey($relativePath)) { throw "Manifest path missing: $relativePath" }
        $file = $actualByPath[$relativePath]
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($file.Length -ne [long]$entry.size -or $hash -cne [string]$entry.sha256) {
            throw "Artifact bytes changed after manifest generation: $relativePath"
        }
        $totalBytes += [long]$file.Length
    }
    if ($totalBytes -ne [long]$record.total_bytes) { throw "Artifact manifest total size mismatch: $($expected.name)" }
}

Write-Output 'PASS: manifest provenance and package bytes match the upload inputs.'
