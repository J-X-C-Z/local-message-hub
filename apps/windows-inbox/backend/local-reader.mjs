import {spawn} from 'node:child_process';
import {watch,readFileSync,existsSync} from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import {createReaderRefresh} from './reader-refresh.mjs';

export function startLocalReader({directory,executable,store,args=[],days=30,refreshOptions}) {
 const privateDirectory=path.join(directory,'local-reading');
 const selection=path.join(privateDirectory,'profile-path.txt');
 if(!existsSync(selection)||!existsSync(path.join(privateDirectory,'database-key.dpapi')))return null;
 store.setReceiverState('source','local-database');
 // M:/Y:/O: and FILEASSIST are application/document feeds, not chat groups.
 if(!store.db.prepare("SELECT 1 FROM receiver_state WHERE key='local_service_category_v1'").get()){
  store.db.exec("UPDATE events SET payload=json_set(payload,'$.ConversationType','service') WHERE json_extract(payload,'$.Source')='local-database' AND json_extract(payload,'$.ConversationType') IS NOT 'service' AND (json_extract(payload,'$.ConversationId') GLOB 'M:*' OR json_extract(payload,'$.ConversationId') GLOB 'Y:*' OR json_extract(payload,'$.ConversationId') GLOB 'O:*' OR json_extract(payload,'$.ConversationId')='FILEASSIST')");
  store.setReceiverState('local_service_category_v1',1);
 }
 store.setReceiverState('heartbeat_at',0);
 let stopped=false,child,watcher,heartbeat,healthy=false,importFailed=false;
 const attachmentWatchers=[];
 const refresh=createReaderRefresh(command=>{if(!child?.stdin.writable)return false;child.stdin.write(command+'\n');return true;},refreshOptions);
 let firstRead;
 const ready=new Promise(resolve=>{firstRead=resolve;});
 const fail=message=>{healthy=false;store.setReceiverState('local_error',message);store.setReceiverState('heartbeat_at',0);};
 try {
  const profile=readFileSync(selection,'utf8').replace(/^\uFEFF/,'').trim();
  child=spawn(executable,[...args,'--directory',privateDirectory,'--days',String(days)],{windowsHide:true,stdio:['pipe','pipe','ignore']});
  const lines=readline.createInterface({input:child.stdout});
  lines.on('line',line=>{
   try {
    const data=JSON.parse(line);
    if(data.type==='snapshot'){
     store.db.exec('BEGIN');
     try{for(const item of data.messages)store.upsertLocal(item.key,item.payload);store.db.exec('COMMIT');}
     catch(error){store.db.exec('ROLLBACK');throw error;}
     store.setReceiverState('local_total',data.total);
     store.setReceiverState('local_conversations',data.conversations);
     store.setReceiverState('last_callback_at',Date.now());
    }
    if(data.type==='health'){
     refresh.done();
     healthy=data.ok===true&&!importFailed;
     store.setReceiverState('local_error',healthy?'':String(importFailed?'本地导入未完成，将自动补查':data.error||'本地读取失败'));
     if (importFailed) refresh.request('attachments');
     importFailed=false;
     store.setReceiverState('heartbeat_at',healthy?Date.now():0);
     if(healthy)store.setReceiverState('last_verified_at',Date.now());
     firstRead();
    }
   } catch {importFailed=true;fail('本地消息导入失败，将自动补查。');}
  });
  child.stdin.on('error',()=>fail('本地读取进程已停止。'));
  child.on('error',()=>fail('无法启动本地消息读取器。'));
  child.on('exit',()=>{if(!stopped)fail('本地消息读取器已停止，请重启收件台。');});
  try {
   watcher=watch(profile,{persistent:false},(event,filename)=>{
    if(filename&&!/^(message|session|user)\.db(?:-wal|-shm)?$/i.test(String(filename)))return;
    refresh.request();
   });
   watcher.on('error',()=>{watcher.close();store.setReceiverState('local_watch_fallback',1);});
  } catch {store.setReceiverState('local_watch_fallback',1);}
  for(const folder of ['Cache','CacheMapping','Emotion']){
   const target=path.join(path.dirname(profile),folder);
   if(!existsSync(target))continue;
   try{const monitor=watch(target,{persistent:false,recursive:true},(event,filename)=>{if(folder==='Cache'&&filename&&!/^(?:File|Image|Video)(?:[\\/]|$)/i.test(String(filename)))return;refresh.request('attachments');});monitor.on('error',()=>store.setReceiverState('local_watch_fallback',1));attachmentWatchers.push(monitor);}catch{store.setReceiverState('local_watch_fallback',1);}
  }
  heartbeat=setInterval(()=>{if(healthy&&!stopped)store.setReceiverState('heartbeat_at',Date.now());},5000).unref();
 }catch{fail('本地读取目录或读取器不可用。');}
 return {ready,async stop(){stopped=true;refresh.stop();clearInterval(heartbeat);watcher?.close();for(const monitor of attachmentWatchers)monitor.close();if(!child||child.exitCode!==null||child.signalCode!==null)return;await new Promise(resolve=>{const timeout=setTimeout(()=>{child.kill();resolve();},2500);child.once('exit',()=>{clearTimeout(timeout);resolve();});child.stdin.end();});}};
}
