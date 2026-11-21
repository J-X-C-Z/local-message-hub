const path=require('node:path');
const fs=require('node:fs');
const {pathToFileURL}=require('node:url');
const {MailClient,configuration,authorizationConfiguration,authorize,validateDraft}=require('./mail-client.cjs');

function installMailCenter({app,BrowserWindow,ipcMain,safeStorage,shell,dialog,getMainWindow,registerEntry=true,onPlatform,fetcher=fetch,vaultAdapter}){
 let window=null,vault=null;const pending=new Map(),sending=new Map();
 const filename=()=>path.join(app.getPath('userData'),'mail-accounts.bin');
 const page=path.join(__dirname,'mail.html'),pageUrl=pathToFileURL(page).href;
 function load(){
  if(vault)return vault;
  if(!safeStorage.isEncryptionAvailable())throw new Error('Windows 本机加密不可用，无法连接邮箱');
  if(!fs.existsSync(filename()))return vault={};
  try{vault=JSON.parse(safeStorage.decryptString(fs.readFileSync(filename())));return vault;}catch{throw new Error('无法读取邮箱授权文件，请检查本机用户或恢复备份');}
 }
 function persist(){
  if(vaultAdapter)return vaultAdapter.save(vault);
  if(!safeStorage.isEncryptionAvailable())throw new Error('Windows 本机加密不可用，邮箱授权未保存');
  fs.mkdirSync(app.getPath('userData'),{recursive:true});const temp=filename()+'.tmp';
  fs.writeFileSync(temp,safeStorage.encryptString(JSON.stringify(vault)),{mode:0o600});fs.renameSync(temp,filename());
 }
 let vaultReady;
 const client=new MailClient({getAccount:id=>load()[id],saveAccount:(id,account)=>{load()[id]=account;return persist();},fetcher});
 function ensure(event){if(!window||event.sender!==window.webContents||event.senderFrame!==window.webContents.mainFrame||event.senderFrame.url!==pageUrl)throw new Error('无权访问邮箱');}
 function validProvider(id){if(!['gmail','outlook'].includes(id))throw new Error('邮箱类型无效');}
 function register(action,handler){ipcMain.handle('mail:'+action,async(event,...args)=>{try{ensure(event);if(vaultAdapter&&!vault)await (vaultReady??=vaultAdapter.load().then(value=>{vault=value;}).finally(()=>{vaultReady=null;}));return {ok:true,data:await handler(...args)};}catch(error){return {ok:false,error:error.message||'邮箱操作失败'};}});}
 function status(){const data=load();return Object.fromEntries(['outlook','gmail'].map(id=>{let setupError='';try{authorizationConfiguration(id,data[id]?.config);}catch(error){setupError=error.message;}return [id,{configured:Boolean(data[id]?.config),clientId:data[id]?.config?.clientId||'',connected:Boolean(data[id]?.tokens),email:data[id]?.email||'',connecting:pending.has(id),authorizationReady:!setupError,setupError}];}));}
 function assertIdle(id){if(pending.has(id)||[...sending.keys()].some(key=>key.startsWith(id+':')&&sending.get(key).pending))throw new Error('邮箱正在连接或发送，请完成后再修改设置');}
 async function saveConfig(id,input){validProvider(id);assertIdle(id);const config=configuration(id,input);load()[id]={config};await persist();return status();}
 register('status',status);
 register('platform',value=>{if(!['wechat','wecom'].includes(value))throw new Error('分区无效');onPlatform?.(value);window?.hide();return true;});
 register('configure',saveConfig);
 register('import-google',async()=>{
  assertIdle('gmail');const result=await dialog.showOpenDialog(window,{title:'选择 Google 桌面应用 OAuth JSON',properties:['openFile'],filters:[{name:'Google OAuth 配置',extensions:['json']}]});
  if(result.canceled)return status();const file=result.filePaths[0];if(fs.statSync(file).size>65536)throw new Error('配置文件过大');
  let credentials;try{credentials=JSON.parse(fs.readFileSync(file,'utf8')).installed;}catch{throw new Error('无法读取 Google OAuth JSON');}
  if(!credentials)throw new Error('请选择“桌面应用”类型的 OAuth JSON，而非 Web 应用或服务账号');
  return saveConfig('gmail',{clientId:credentials.client_id,clientSecret:credentials.client_secret});
 });
 register('connect',async id=>{
  validProvider(id);assertIdle(id);const existing=load()[id];if(!existing?.config)throw new Error('此邮箱尚未接入收件台。请先完成应用授权配置，或点击“打开官方邮箱”直接使用。');
  const config=authorizationConfiguration(id,existing.config);
  pending.set(id,()=>{});
  try{
   const tokens=await authorize(id,config,url=>shell.openExternal(url),{fetcher,onPending:cancel=>{if(cancel)pending.set(id,cancel);}});
   const old=load()[id];load()[id]={config:existing.config,tokens};
   try{load()[id].email=await client.profile(id);await persist();}catch(error){load()[id]=old;throw error;}
   return status();
  }finally{pending.delete(id);}
 });
 register('cancel',id=>{validProvider(id);pending.get(id)?.();return true;});
 register('disconnect',async id=>{validProvider(id);assertIdle(id);if(load()[id]){load()[id]={config:load()[id].config};await persist();}return status();});
 register('list',(id,folder,cursor)=>{validProvider(id);return client.list(id,folder,cursor);});
 register('get',(id,messageId)=>{validProvider(id);return client.get(id,messageId);});
 register('send',async(id,requestId,input)=>{
  validProvider(id);if(typeof requestId!=='string'||!/^[a-f0-9-]{36}$/i.test(requestId))throw new Error('发送请求编号无效');
  const key=id+':'+requestId;if(sending.has(key))return sending.get(key).promise;
  if(sending.size>=500)throw new Error('本次会话发送请求已达上限，请重启应用');
  if(pending.has(id))throw new Error('请等待邮箱连接完成');
  const draft=validateDraft(input);if(!load()[id]?.tokens)throw new Error('请先连接邮箱');
  const job={pending:true};
  job.promise=(async()=>{
   const choice=await dialog.showMessageBox(window,{type:'question',title:'确认发送邮件',message:`从 ${load()[id].email} 发送邮件？`,detail:`收件人：${draft.to.join(', ')}\n抄送：${draft.cc.join(', ')||'无'}\n密送：${draft.bcc.join(', ')||'无'}\n主题：${draft.subject}\n\n正文：\n${draft.body.slice(0,300)}${draft.body.length>300?'…':''}`,buttons:['取消','发送'],defaultId:0,cancelId:0,noLink:true});
   if(choice.response!==1)return {canceled:true};
   return client.send(id,draft);
  })().finally(()=>{job.pending=false;});
  sending.set(key,job);return job.promise;
 });
 register('open-web',id=>{validProvider(id);return shell.openExternal(id==='gmail'?'https://mail.google.com/':'https://outlook.live.com/mail/');});
 function open(){
  if(window&&!window.isDestroyed()){window.show();window.focus();return;}
  window=new BrowserWindow({width:1280,height:850,minWidth:950,minHeight:650,title:'邮箱中心 · 企微收件台',parent:getMainWindow(),show:false,autoHideMenuBar:true,backgroundColor:'#f4f7fb',webPreferences:{preload:path.join(__dirname,'mail-preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,devTools:!app.isPackaged}});
  window.once('ready-to-show',()=>window?.show());
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));window.webContents.on('will-navigate',event=>event.preventDefault());window.webContents.on('will-attach-webview',event=>event.preventDefault());
  window.on('closed',()=>{for(const cancel of pending.values())cancel();window=null;});window.loadFile(page);
 }
 if(registerEntry)ipcMain.on('desktop:mail',event=>{const main=getMainWindow();if(main&&event.sender===main.webContents&&event.senderFrame===main.webContents.mainFrame)open();});
 app.on('before-quit',()=>{for(const cancel of pending.values())cancel();});
 return {open};
}
module.exports={installMailCenter};
