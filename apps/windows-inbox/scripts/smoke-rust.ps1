param([string]$Executable='release-rust/WecomInbox.exe')
$ErrorActionPreference='Stop'
$exe=(Resolve-Path -LiteralPath $Executable).Path
$profile=Join-Path $env:TEMP ('wecom-rust-smoke-'+[guid]::NewGuid().ToString('N'))
$trace=$profile+'.log'
New-Item -ItemType Directory -Path $profile | Out-Null
@'
import path from 'node:path';
import {EventStore} from './backend/wecom-mcp/src/store.js';
const store=new EventStore(path.join(process.env.INBOX_TEST_PROFILE,'messages.db'));
store.add('fixture:rust',{CreateTime:Math.floor(Date.now()/1000),FromUserName:'fixture',ConversationId:'fixture',ConversationName:'Rust smoke',Platform:'wechat',MsgType:'text',Content:'Rust smoke message'});
store.close();
'@ | ForEach-Object { $env:INBOX_TEST_PROFILE=$profile; $_ | node --input-type=module }
if($LASTEXITCODE -ne 0){throw 'Fixture setup failed'}
$process=Start-Process -FilePath $exe -ArgumentList ('--profile "'+$profile+'" --startup-log "'+$trace+'" --smoke --preload-aihot') -WindowStyle Hidden -PassThru
try{
    $wanted=@('main-window','main-dom','service-ready','test-preview','test-render','test-stream','test-settings','test-mail','test-campus','aihot-shown','aihot-home','test-aihot-inline-link')
    $timer=[Diagnostics.Stopwatch]::StartNew()
    $lines=@()
    while($timer.Elapsed.TotalSeconds -lt 30){
        if(Test-Path -LiteralPath $trace){$lines=@(Get-Content -LiteralPath $trace)}
        $missing=@($wanted | Where-Object { $_ -notin ($lines -replace '^\d+ ','') })
        if(!$missing -or ($lines -match 'test-fail-') -or $process.HasExited){break}
        Start-Sleep -Milliseconds 150
    }
    $lines
    if($missing){throw "Rust smoke checks not observed: $($missing -join ', ')"}
}finally{
    if(!$process.HasExited){
        $process.CloseMainWindow() | Out-Null
        if(!$process.WaitForExit(7000)){Stop-Process -Id $process.Id}
    }
}
