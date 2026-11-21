import {spawn} from 'node:child_process';
import {watch,mkdirSync,existsSync} from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import {createHash} from 'node:crypto';
import {createReaderRefresh} from './reader-refresh.mjs';

export function startWeChatReader({directory,executable,store,days=30}){
 const privateDirectory=path.join(directory,'wechat-reading');mkdirSync(privateDirectory,{recursive:true});
 let child,watcher,fileWatcher,videoWatcher,recordWatcher,stopped=false,importFailed=false,watchedPath;
 const state=(key,value)=>store.setReceiverState('wechat_'+key,value);
 const refresh=createReaderRefresh(command=>{if(!child?.stdin.writable)return false;child.stdin.write(command+'\n');return true;});
 state('status','正在读取微信');state('error','');
 child=spawn(executable,['--directory',privateDirectory,'--days',String(days)],{windowsHide:true,stdio:['pipe','pipe','ignore']});
 readline.createInterface({input:child.stdout}).on('line',line=>{
  try{
   const data=JSON.parse(line);
   if(data.type==='profile'){
    const cached = store.db.prepare("SELECT value FROM receiver_state WHERE key='wechat_social'").get()?.value;
    if (cached && JSON.parse(cached).account !== data.account) {
     state('social', JSON.stringify({account:data.account, avatars:[], posts:null}));
     state('social_revision','');
    }
    if (watchedPath !== data.path) {
     watcher?.close();fileWatcher?.close();videoWatcher?.close();recordWatcher?.close();watchedPath=data.path;
     try {
      watcher=watch(data.path,{recursive:true,persistent:false},(_,name)=>{if(!name||/\.db(?:-wal)?$/i.test(String(name)))refresh.request();});
      watcher.on('error',()=>{watcher.close();state('watch_fallback',1);});
     } catch {state('watch_fallback',1);}
     const videoRoot=path.join(path.dirname(data.path),'msg','video');
     if(existsSync(videoRoot))try {
      videoWatcher=watch(videoRoot,{recursive:true,persistent:false},(_,name)=>{
       if(!name||!/\.[a-z0-9]+$/i.test(String(name))||/\.mp4$/i.test(String(name)))refresh.request();
      });
      videoWatcher.on('error',()=>videoWatcher?.close());
     }catch{}
     const fileRoot=path.join(path.dirname(data.path),'msg','file');
     if(existsSync(fileRoot))try{
      fileWatcher=watch(fileRoot,{recursive:true,persistent:false},()=>refresh.request());
      fileWatcher.on('error',()=>fileWatcher?.close());
     }catch{}
     const cacheRoot=path.join(path.dirname(data.path),'cache');
     if(existsSync(cacheRoot))try{
      recordWatcher=watch(cacheRoot,{recursive:true,persistent:false},(_,name)=>{
       if(!name||/[/\\]Message[/\\].+[/\\](?:RecTmp|ImageTemp|Thumb|Bubble)(?:[/\\]|$)/i.test(String(name)))refresh.request();
      });
      recordWatcher.on('error',()=>recordWatcher?.close());
     }catch{}
    }
   }
   if(data.type==='snapshot'){
    let changed=false;
    store.db.exec('BEGIN');try{for(const item of data.messages)if(store.upsertLocal(item.key,item.payload))changed=true;store.db.exec('COMMIT');}catch(error){store.db.exec('ROLLBACK');throw error;}
    if(changed)state('status','正在导入微信消息');
   }
   if (data.type==='social') {
    const previous = store.db.prepare("SELECT value FROM receiver_state WHERE key='wechat_social'").get()?.value;
    const cached = previous ? JSON.parse(previous) : null;
    const social = {account:data.account, avatars:data.avatars.length || cached?.account !== data.account ? data.avatars : cached.avatars, posts:data.posts ?? (cached?.account === data.account ? cached.posts : null)};
    const serialized = JSON.stringify(social);
    if (serialized !== previous) store.setReceiverState('wechat_social',serialized);
    const revision = createHash('sha256').update(serialized).digest('hex');
    state('social_revision',revision);
   }
   if(data.type==='health'||data.type==='progress'){
    const healthy=data.ok&&!importFailed;
    state('status',healthy?'微信本机监听中':'微信读取失败');state('error',data.error||(importFailed?'微信导入未完成，将自动补查':''));state('updated_at',Date.now());
    if(healthy&&Number.isInteger(data.total)){state('total',data.total);state('conversations',data.conversations);state('locked',data.locked||0);}
    if(data.type==='health'){
     if(importFailed)refresh.request('attachments');
     importFailed=false;refresh.done();
    }
   }
  }catch{importFailed=true;state('error','微信消息导入失败，将自动补查');}
 });
 child.stdin.on('error',()=>{});
 child.on('error',()=>{state('error','无法启动微信读取器');});
 child.on('exit',()=>{if(!stopped){state('status','微信读取已停止');state('error','请从应用菜单重启微信接收');}});
 return{async stop(){stopped=true;refresh.stop();watcher?.close();fileWatcher?.close();videoWatcher?.close();recordWatcher?.close();if(child.exitCode!==null||child.signalCode!==null)return;await new Promise(resolve=>{const timeout=setTimeout(()=>{child.kill();resolve();},4000);child.once('exit',()=>{clearTimeout(timeout);resolve();});child.stdin.end();});}};
}
