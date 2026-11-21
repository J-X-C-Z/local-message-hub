param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
foreach ($reader in @(
    @{ Directory = 'local-reader'; Name = 'WecomLocalReader' },
    @{ Directory = 'wechat-reader'; Name = 'WeChatLocalReader' }
)) {
    $source = Join-Path $root $reader.Directory
    $output = Join-Path $root ('runtime/' + $reader.Directory)
    $work = Join-Path $root ('build/python/' + $reader.Directory)
    & $Python -m PyInstaller --noconfirm --onefile --noupx --name $reader.Name `
        --workpath $work --specpath $work --distpath $output `
        --paths (Join-Path $root 'reader-common') --paths (Join-Path $source 'vendor') `
        (Join-Path $source 'reader.py')
    if ($LASTEXITCODE -ne 0) { throw "Could not build $($reader.Name)." }
}
Copy-Item -LiteralPath (Join-Path $root 'local-reader/vendor/LICENSE') `
    -Destination (Join-Path $root 'runtime/local-reader/LICENSE-wecom-agent.txt')
Copy-Item -LiteralPath (Join-Path $root 'wechat-reader/NOTICE.md') `
    -Destination (Join-Path $root 'runtime/wechat-reader/NOTICE.md')
Copy-Item -LiteralPath (Join-Path $root 'wechat-reader/vendor/LICENSE') `
    -Destination (Join-Path $root 'runtime/wechat-reader/LICENSE-wechatauto.txt')
