const path=require('node:path');
const fs=require('node:fs');
const os=require('node:os');
const {createInterface}=require('node:readline');
const {EventEmitter}=require('node:events');
const {DatabaseSync}=require('node:sqlite');
const {randomBytes}=require('node:crypto');
const {validateSettings}=require('../desktop/settings-schema.cjs');
const {installMailCenter}=require('../desktop/mail-desktop.cjs');
const {installAttachments}=require('../desktop/attachments-desktop.cjs');
const {setAttachmentMode,attachmentMode}=require('../desktop/storage-preferences.cjs');
const {CampusMonitor,wechatKey}=require('./campus.mjs');

function startDesktopPipe({receive,send,stop}){
 let config,monitor,serviceInit,restarting=false;
 let counter=0;
 const handlers=new Map(),nativeCalls=new Map(),windows=new Map();
 const roleOf=action=>action.startsWith('mail:')?'mail':'main';
 const native=(action,...args)=>new Promise((resolve,reject)=>{
  const id=++counter;
  nativeCalls.set(id,{resolve,reject});send({type:'native',id,action,args});
 });
 const app=new EventEmitter();
 app.getPath=name=>name==='userData'?config.dataDirectory:path.join(os.homedir(),'Downloads');
 const ipcMain={handle:(name,fn)=>handlers.set(name,fn)};
 const shell={openExternal:url=>native('url',url),openPath:filename=>native('path',filename),showItemInFolder:filename=>void native('reveal',filename)};
 const dialog={showOpenDialog:(_window,options)=>native('pick',options.properties?.includes('openDirectory')?'folder':'json'),
  showSaveDialog:(_window,options)=>native('save',options.defaultPath),showMessageBox:(_window,options)=>native('confirm',options.message,options.detail)};
 const safeStorage={isEncryptionAvailable:()=>true,decryptString:bytes=>native('decrypt',bytes.toString('base64'))};
 function win(role){
  if(windows.has(role))return windows.get(role);
  const view=new EventEmitter();view.mainFrame={url:role==='main'?'wecom-inbox://app/':''};
  view.setWindowOpenHandler=()=>{};
  const value=new EventEmitter();value.webContents=view;value.isDestroyed=()=>false;
  value.show=()=>{};value.focus=()=>{};value.hide=()=>void native('hide',role);
  value.loadFile=page=>{view.mainFrame.url=require('node:url').pathToFileURL(page).href;};
  windows.set(role,value);return value;
 }
 async function boot(message){
  config=message;
  installAttachments({app,ipcMain,shell,dialog,getMainWindow:()=>win('main'),getOrigin:()=> 'wecom-inbox://app'});
  const mailPath=path.join(config.dataDirectory,'mail-accounts.bin');
  const mail=installMailCenter({app,BrowserWindow:function(){return win('mail');},ipcMain,
   safeStorage,
   shell,dialog,getMainWindow:()=>win('main'),registerEntry:false,onPlatform:value=>void native('platform',value),
   vaultAdapter:{async load(){
    if(!fs.existsSync(mailPath))return {};
    return JSON.parse(await safeStorage.decryptString(fs.readFileSync(mailPath)));
   },async save(data){await native('vault-write','mail-accounts.bin',JSON.stringify(data));}}});
  mail.open();
  monitor=new CampusMonitor({directory:config.dataDirectory,onUpdate:()=>send({type:'event',role:'campus',event:'campus-updated'}),readWeChat:accounts=>{
   let database;try{
    database=new DatabaseSync(path.join(config.dataDirectory,'messages.db'),{readOnly:true});
    database.exec('PRAGMA busy_timeout=250');database.function('campus_account',{deterministic:true},wechatKey);
    const names=accounts.map(wechatKey);
    return database.prepare(`SELECT payload FROM events WHERE received_at > ? AND dedup_key LIKE 'wechat:%' AND json_valid(payload) AND json_extract(payload,'$.ConversationType') IN ('official','service') AND campus_account(json_extract(payload,'$.ConversationName')) IN (${names.map(()=>'?').join(',')}) AND payload LIKE '%mp.weixin.qq.com%' ORDER BY received_at DESC LIMIT 3000`).all(Date.now()-180*86400000,...names).map(row=>JSON.parse(row.payload));
   }finally{database?.close();}
  }});
  setTimeout(()=>monitor?.start(),3000).unref();
  const campus={snapshot:()=>monitor.snapshot(),refresh:async()=>{await monitor.refresh();return monitor.snapshot();},read:ids=>monitor.markRead(ids),
   detail:id=>monitor.detail(id),setSource:(id,enabled)=>monitor.setSource(id,enabled),addSource:(type,name,url)=>monitor.addSource(type,name,url),
   removeSource:id=>monitor.removeSource(id),open:url=>shell.openExternal(monitor.openAddress(url)),copy:url=>native('clipboard',monitor.openAddress(url))};
  for(const [action,fn] of Object.entries(campus))handlers.set('campus:'+action,async(_event,...args)=>({ok:true,data:await fn(...args)}));
  handlers.set('settings:load',async()=>{
   const current=await native('vault-read','connection.bin');
   let draft=await native('vault-read','callback-draft.bin');
   if(!current&&!draft){
    draft=JSON.stringify({callbackToken:randomBytes(16).toString('hex'),callbackAesKey:randomBytes(32).toString('base64').slice(0,-1),callbackPort:8787,retentionDays:30});
    await native('vault-write','callback-draft.bin',draft);
   }
   return {settings:JSON.parse(current||draft||'{}'),encryptionAvailable:true,generatedDraft:!current};
  });
  handlers.set('settings:save',async(_event,input)=>{
   const settings=validateSettings(input);
   await native('vault-write','connection.bin',JSON.stringify(settings));
   return {ok:true,restart:true};
  });
  handlers.set('settings:attachment-mode',()=>attachmentMode(config.dataDirectory));
  handlers.set('settings:set-attachment-mode',(_event,mode)=>setAttachmentMode(config.dataDirectory,mode));
  handlers.set('settings:select-wechat',async()=>{
   const result=await native('pick','folder');if(result.canceled)return {ok:false,canceled:true};
   const selected=result.filePaths[0];
   if(!fs.existsSync(path.join(selected,'db_storage')))throw new Error('请选择包含 db_storage 的微信账号目录');
   const directory=path.join(config.dataDirectory,'wechat-reading');fs.mkdirSync(directory,{recursive:true});
   fs.writeFileSync(path.join(directory,'account-path.txt'),selected,'utf8');
   fs.rmSync(path.join(directory,'database-keys.dpapi'),{force:true});return {ok:true,restart:true};
  });
  await receive(message);
 }
 const input=createInterface({input:process.stdin,crlfDelay:Infinity});
 function shutdown(){if(restarting)return;restarting=true;monitor?.stop();app.emit('before-quit');void stop();}
 input.on('close',shutdown);
 input.on('line',line=>{
  let message;try{message=JSON.parse(line);}catch{return;}
  if(message.type==='shutdown'){shutdown();return;}
  if(message.type==='init'&&!serviceInit){serviceInit=boot(message);serviceInit.catch(error=>{send({type:'fatal',error:error.message});shutdown();});return;}
  if(message.type==='native-result'){
   const call=nativeCalls.get(message.id);if(!call)return;
   nativeCalls.delete(message.id);if(message.error)call.reject(new Error(message.error));else call.resolve(message.value);return;
  }
  if(message.type==='call'&&serviceInit)void serviceInit.then(async()=>{
   const handler=handlers.get(message.action);
   if(!handler)throw new Error('操作不受支持');
   const sender=win(roleOf(message.action)).webContents;
   const value=await handler({sender,senderFrame:sender.mainFrame},...message.args);
   send({type:'reply',id:message.id,role:message.role,value:value??null});
  }).catch(error=>send({type:'reply',id:message.id,role:message.role,error:error.message}));
 });
}

module.exports={startDesktopPipe};
