$ErrorActionPreference = 'Stop'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('lmh-manifest-test-' + [guid]::NewGuid().ToString('N'))
$portable = Join-Path $root 'portable'
$nested = Join-Path $portable 'deep'
$hidden = Join-Path $portable '.metadata'
$source = Join-Path $root 'LocalMessageHub-source.zip'
$manifestName = 'LocalMessageHub-artifact-manifest.json'
$output = Join-Path $root 'artifact-manifest.json'
$second = Join-Path $root 'artifact-manifest-second.json'
$uploaded = Join-Path $root 'uploaded-release'
$outside = Join-Path $root 'outside'
$junction = Join-Path $portable 'linked'
$writer = Join-Path $PSScriptRoot 'write-package-manifest.ps1'
$verifier = Join-Path $PSScriptRoot 'verify-package-manifest.ps1'

function Assert-HostedManifestProvenance([string]$Workflow) {
    $steps = @(
        [regex]::Match($Workflow, '(?ms)^      - name: Write package provenance manifest\r?\n(?:(?!^      - name: ).)*').Value,
        [regex]::Match($Workflow, '(?ms)^      - name: Verify package manifest against upload inputs\r?\n(?:(?!^      - name: ).)*').Value,
        [regex]::Match($Workflow, '(?ms)^      - name: Verify downloaded artifact bytes and provenance\r?\n(?:(?!^      - name: ).)*').Value
    )
    foreach ($step in $steps) {
        if (-not $step -or
            $step -notmatch '(?m)^          COMMIT_SHA:\s+\$\{\{\s*github\.sha\s*\}\}\s*$' -or
            $step -notmatch '(?m)^          WORKFLOW_RUN_ID:\s+\$\{\{\s*github\.run_id\s*\}\}\s*$' -or
            $step -notmatch '(?m)^          -CommitSha \$env:COMMIT_SHA\s*$' -or
            $step -notmatch '(?m)^          -WorkflowRunId \$env:WORKFLOW_RUN_ID\s*$' -or
            $step -notmatch '(?m)^          -BuildContext hosted-ci\s*$') {
            throw 'Hosted artifact manifest creation and both verification passes must bind to the same GitHub commit, run ID, and hosted-ci context.'
        }
    }
}

New-Item -ItemType Directory -Path $nested,$hidden -Force | Out-Null
try {
    [System.IO.File]::WriteAllText((Join-Path $portable 'ui.txt'), 'portable fixture')
    [System.IO.File]::WriteAllBytes((Join-Path $nested 'mcp.bin'), [byte[]](0, 1, 2, 255))
    [System.IO.File]::WriteAllText((Join-Path $hidden 'fixture.txt'), 'hidden package fixture')
    [System.IO.File]::WriteAllText($source, 'source fixture')

    $arguments = @{
        PortablePath = $portable
        SourceBundlePath = $source
        CommitSha = '0123456789abcdef0123456789abcdef01234567'
        WorkflowRunId = 'test-run-42'
        BuildContext = 'synthetic-test'
    }
    & $writer @arguments -OutputPath $output | Out-Null
    & $writer @arguments -OutputPath $second | Out-Null
    $firstText = [System.IO.File]::ReadAllText($output)
    $secondText = [System.IO.File]::ReadAllText($second)
    if ($firstText -cne $secondText) { throw 'Manifest output is not deterministic.' }
    if ($firstText.Contains($root.Replace('\', '/'))) { throw 'Manifest exposed a local absolute path.' }
    $lockedManifest = [System.IO.File]::Open($output, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
    $replaceFailurePreservedManifest = $false
    try {
        try { & $writer @arguments -OutputPath $output | Out-Null }
        catch { $replaceFailurePreservedManifest = $true }
    } finally {
        $lockedManifest.Dispose()
    }
    if (-not $replaceFailurePreservedManifest -or
        [System.IO.File]::ReadAllText($output) -cne $firstText) {
        throw 'Failed atomic manifest replacement damaged the previous manifest.'
    }
    if (@(Get-ChildItem -LiteralPath $root -Filter '.artifact-manifest.json.*.tmp' -Force).Count -gt 0) {
        throw 'Failed atomic manifest replacement left a staged temporary file.'
    }

    $manifest = Get-Content -LiteralPath $output -Raw | ConvertFrom-Json
    if ($manifest.commit_sha -ne $arguments.CommitSha -or $manifest.workflow_run_id -ne 'test-run-42' -or $manifest.build_context -ne 'synthetic-test') {
        throw 'Manifest provenance fields do not match their inputs.'
    }
    if ($manifest.packages.Count -ne 2 -or $manifest.packages[0].file_count -ne 3 -or $manifest.packages[1].file_count -ne 1) {
        throw 'Manifest package inventory is incomplete.'
    }
    $workflow = Get-Content -LiteralPath (Join-Path $PSScriptRoot '../.github/workflows/local-hub-ci.yml') -Raw
    Assert-HostedManifestProvenance -Workflow $workflow
    $driftedWorkflow = $workflow.Replace('WORKFLOW_RUN_ID: ${{ github.run_id }}', 'WORKFLOW_RUN_ID: ${{ github.actor }}')
    $provenanceDriftRejected = $false
    try { Assert-HostedManifestProvenance -Workflow $driftedWorkflow }
    catch { $provenanceDriftRejected = $true }
    if (-not $provenanceDriftRejected) { throw 'Hosted provenance guard accepted a workflow-run ID drift.' }
    $uploadStep = [regex]::Match($workflow, '(?ms)^      - name: Upload Windows release packages\r?\n(?:(?!^      - name: ).)*').Value
    if ($uploadStep -notmatch '(?m)^          include-hidden-files:\s*true\s*$') {
        throw 'Release artifact upload must preserve manifest-covered hidden files.'
    }
    $downloadStep = [regex]::Match($workflow, '(?ms)^      - name: Download and verify uploaded release packages\r?\n(?:(?!^      - name: ).)*').Value
    $downloadVerifyStep = [regex]::Match($workflow, '(?ms)^      - name: Verify downloaded artifact bytes and provenance\r?\n(?:(?!^      - name: ).)*').Value
    $uploadIndex = $workflow.IndexOf('      - name: Upload Windows release packages', [System.StringComparison]::Ordinal)
    $downloadIndex = $workflow.IndexOf('      - name: Download and verify uploaded release packages', [System.StringComparison]::Ordinal)
    $downloadVerifyIndex = $workflow.IndexOf('      - name: Verify downloaded artifact bytes and provenance', [System.StringComparison]::Ordinal)
    if ($uploadIndex -lt 0 -or $downloadIndex -le $uploadIndex -or $downloadVerifyIndex -le $downloadIndex) {
        throw 'Release artifact must be uploaded, downloaded, and verified in that order.'
    }
    Write-Output 'PASS: hosted manifest creation and both verification passes bind to the same commit/run provenance.'
    foreach ($contract in @(
        @{ Step = $uploadStep; Pattern = '(?m)^          name:\s*LocalMessageHub-windows-release\s*$'; Error = 'Upload artifact name changed.' },
        @{ Step = $uploadStep; Pattern = '(?m)^            output/windows/LocalMessageHub-portable-ci/\s*$'; Error = 'Portable upload path changed.' },
        @{ Step = $uploadStep; Pattern = '(?m)^            output/windows/LocalMessageHub-source\.zip\s*$'; Error = 'Source upload path changed.' },
        @{ Step = $uploadStep; Pattern = '(?m)^            output/windows/LocalMessageHub-artifact-manifest\.json\s*$'; Error = 'Manifest upload path changed.' },
        @{ Step = $downloadStep; Pattern = '(?m)^          name:\s*LocalMessageHub-windows-release\s*$'; Error = 'Downloaded artifact name must match the upload.' },
        @{ Step = $downloadStep; Pattern = '(?m)^          path:\s*output/windows/uploaded-release\s*$'; Error = 'Downloaded artifact path changed.' },
        @{ Step = $downloadVerifyStep; Pattern = '(?m)^          -ManifestPath ./output/windows/uploaded-release/LocalMessageHub-artifact-manifest\.json\s*$'; Error = 'Downloaded manifest is not verified.' },
        @{ Step = $downloadVerifyStep; Pattern = '(?m)^          -PortablePath ./output/windows/uploaded-release/LocalMessageHub-portable-ci\s*$'; Error = 'Downloaded portable package is not verified.' },
        @{ Step = $downloadVerifyStep; Pattern = '(?m)^          -SourceBundlePath ./output/windows/uploaded-release/LocalMessageHub-source\.zip\s*$'; Error = 'Downloaded source bundle is not verified.' }
    )) {
        if ($contract.Step -notmatch $contract.Pattern) { throw $contract.Error }
    }
    $entry = $manifest.packages[0].files | Where-Object path -eq 'deep/mcp.bin'
    $expectedHash = (Get-FileHash -LiteralPath (Join-Path $nested 'mcp.bin') -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not $entry -or $entry.sha256 -ne $expectedHash -or $entry.size -ne 4) {
        throw 'Manifest file hash or byte count is incorrect.'
    }
    & $verifier -ManifestPath $output -PortablePath $portable -SourceBundlePath $source `
        -CommitSha $arguments.CommitSha -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null

    $uploadedPortable = Join-Path $uploaded 'LocalMessageHub-portable-ci'
    New-Item -ItemType Directory -Path $uploadedPortable -Force | Out-Null
    Get-ChildItem -LiteralPath $portable -Force | Copy-Item -Destination $uploadedPortable -Recurse
    Copy-Item -LiteralPath $source -Destination (Join-Path $uploaded 'LocalMessageHub-source.zip')
    Copy-Item -LiteralPath $output -Destination (Join-Path $uploaded $manifestName)
    & $verifier -ManifestPath (Join-Path $uploaded $manifestName) `
        -PortablePath $uploadedPortable -SourceBundlePath (Join-Path $uploaded 'LocalMessageHub-source.zip') `
        -CommitSha $arguments.CommitSha -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null

    $uploadedSource = Join-Path $uploaded 'LocalMessageHub-source.zip'
    [System.IO.File]::WriteAllText($uploadedSource, 'mutated source archive')
    $sourceMutationRejected = $false
    try {
        & $verifier -ManifestPath (Join-Path $uploaded $manifestName) `
            -PortablePath $uploadedPortable -SourceBundlePath $uploadedSource `
            -CommitSha $arguments.CommitSha -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null
    } catch {
        $sourceMutationRejected = $true
    }
    if (-not $sourceMutationRejected) { throw 'Round-trip verifier accepted mutated source bundle bytes.' }
    Copy-Item -LiteralPath $source -Destination $uploadedSource -Force

    [System.IO.File]::WriteAllText((Join-Path $uploadedPortable 'unexpected.bin'), 'not in the inventory')
    $extraFileRejected = $false
    try {
        & $verifier -ManifestPath (Join-Path $uploaded $manifestName) `
            -PortablePath $uploadedPortable -SourceBundlePath $uploadedSource `
            -CommitSha $arguments.CommitSha -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null
    } catch {
        $extraFileRejected = $true
    }
    if (-not $extraFileRejected) { throw 'Round-trip verifier accepted an unmanifested package file.' }
    Remove-Item -LiteralPath (Join-Path $uploadedPortable 'unexpected.bin') -Force

    Remove-Item -LiteralPath (Join-Path $uploadedPortable 'deep/mcp.bin') -Force
    $missingFileRejected = $false
    try {
        & $verifier -ManifestPath (Join-Path $uploaded $manifestName) `
            -PortablePath $uploadedPortable -SourceBundlePath $uploadedSource `
            -CommitSha $arguments.CommitSha -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null
    } catch {
        $missingFileRejected = $true
    }
    if (-not $missingFileRejected) { throw 'Round-trip verifier accepted a missing package file.' }
    Copy-Item -LiteralPath (Join-Path $nested 'mcp.bin') -Destination (Join-Path $uploadedPortable 'deep/mcp.bin') -Force

    $uploadedManifest = Join-Path $uploaded $manifestName
    $originalManifestText = [System.IO.File]::ReadAllText($uploadedManifest)
    $unsafeManifest = $originalManifestText | ConvertFrom-Json
    $unsafeManifest.packages[0].files[0].path = '../escape.bin'
    [System.IO.File]::WriteAllText($uploadedManifest, (ConvertTo-Json -InputObject $unsafeManifest -Depth 8))
    $unsafePathRejected = $false
    try {
        & $verifier -ManifestPath $uploadedManifest `
            -PortablePath $uploadedPortable -SourceBundlePath $uploadedSource `
            -CommitSha $arguments.CommitSha -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null
    } catch {
        $unsafePathRejected = $true
    }
    if (-not $unsafePathRejected) { throw 'Round-trip verifier accepted a path-traversal manifest entry.' }
    [System.IO.File]::WriteAllText($uploadedManifest, $originalManifestText)

    $wrongProvenanceRejected = $false
    try {
        & $verifier -ManifestPath (Join-Path $uploaded $manifestName) `
            -PortablePath $uploadedPortable -SourceBundlePath (Join-Path $uploaded 'LocalMessageHub-source.zip') `
            -CommitSha 'abcdef0123456789abcdef0123456789abcdef01' `
            -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null
    } catch {
        $wrongProvenanceRejected = $true
    }
    if (-not $wrongProvenanceRejected) { throw 'Round-trip verifier accepted mismatched commit provenance.' }

    [System.IO.File]::WriteAllText((Join-Path $uploadedPortable 'deep/mcp.bin'), 'mutated upload')
    $uploadedMutationRejected = $false
    try {
        & $verifier -ManifestPath (Join-Path $uploaded $manifestName) `
            -PortablePath $uploadedPortable -SourceBundlePath (Join-Path $uploaded 'LocalMessageHub-source.zip') `
            -CommitSha $arguments.CommitSha -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null
    } catch {
        $uploadedMutationRejected = $true
    }
    if (-not $uploadedMutationRejected) { throw 'Round-trip verifier accepted mutated uploaded bytes.' }

    [System.IO.File]::WriteAllBytes((Join-Path $nested 'mcp.bin'), [System.Text.Encoding]::ASCII.GetBytes('evil'))
    $mutationRejected = $false
    try {
        & $verifier -ManifestPath $output -PortablePath $portable -SourceBundlePath $source `
            -CommitSha $arguments.CommitSha -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null
    } catch {
        $mutationRejected = $true
    }
    if (-not $mutationRejected) { throw 'Manifest verifier accepted package bytes changed after inventory.' }

    New-Item -ItemType Directory -Path $outside | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $outside 'untracked.txt'), 'not part of the manifest')
    New-Item -ItemType Junction -Path $junction -Target $outside | Out-Null
    foreach ($script in @($writer, $verifier)) {
        $reparseRejected = $false
        try {
            if ($script -eq $writer) {
                & $script @arguments -OutputPath $second | Out-Null
            } else {
                & $script -ManifestPath $output -PortablePath $portable -SourceBundlePath $source `
                    -CommitSha $arguments.CommitSha -WorkflowRunId $arguments.WorkflowRunId -BuildContext $arguments.BuildContext | Out-Null
            }
        } catch {
            $reparseRejected = $true
        }
        if (-not $reparseRejected) { throw "Manifest script accepted package reparse point: $script" }
    }
    Write-Output 'PASS: package manifest binds deterministic inventory hashes to commit and run metadata.'
} finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
