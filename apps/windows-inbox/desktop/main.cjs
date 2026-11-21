// Detached launchers may close their output pipes while Electron emits a warning.
for(const stream of [process.stdout,process.stderr])stream.on('error',error=>{if(error.code!=='EPIPE')throw error;});
const {app,BrowserWindow,WebContentsView,Menu,ipcMain,safeStorage,session,shell,clipboard,dialog,protocol}=require('electron');
const {fork}=require('node:child_process');
const {readFileSync,writeFileSync,renameSync,existsSync,mkdirSync}=require('node:fs');
const path=require('node:path');
const {randomBytes}=require('node:crypto');
const {validateSettings}=require('./settings-schema.cjs');
const {attachmentMode,setAttachmentMode}=require('./storage-preferences.cjs');
const {UI_ORIGIN,createWorkbenchHandler}=require('./workbench-protocol.cjs');
const {installAIHotReader}=require('./aihot-desktop.cjs');
protocol.registerSchemesAsPrivileged([{scheme:'wecom-inbox',privileges:{standard:true,secure:true,supportFetchAPI:true,stream:true,corsEnabled:true}}]);

app.setName('企微收件台');
const explicitProfile=app.commandLine.getSwitchValue('user-data-dir');
if(explicitProfile&&path.isAbsolute(explicitProfile)){
 mkdirSync(explicitProfile,{recursive:true});app.setPath('userData',explicitProfile);app.setPath('sessionData',path.join(explicitProfile,'session'));
}
app.setAppUserModelId('local.wecom.inbox');
const hasLock=app.requestSingleInstanceLock();
if(!hasLock){app.quit();}else{
let mainWindow,settingsWindow,backend;
let currentOrigin='';let currentSettings=null;let startupError='';let quitting=false;
const accessToken=randomBytes(32).toString('hex');
const root=path.join(__dirname,'..');
const settingsPage=path.join(__dirname,'settings.html');
require('./attachments-desktop.cjs').installAttachments({app,ipcMain,shell,dialog,getMainWindow:()=>mainWindow,getOrigin:()=>UI_ORIGIN});
let backendOriginReady,resolveBackendOrigin,rejectBackendOrigin;
function resetBackendOrigin(){backendOriginReady=new Promise((resolve,reject)=>{resolveBackendOrigin=resolve;rejectBackendOrigin=reject;});backendOriginReady.catch(()=>{});}
resetBackendOrigin();
let mailCenter;let activePlatform='wechat';
let campusCenter;
let aiHotReader;
function openCampusCenter(){campusCenter?.open();}
function openAIHotReader(){if(!quitting)aiHotReader?.open();}
function openMailCenter(){
 mailCenter||=require('./mail-desktop.cjs').installMailCenter({app,BrowserWindow,ipcMain,safeStorage,shell,dialog,getMainWindow:()=>mainWindow,registerEntry:false,onPlatform:value=>{activePlatform=value;mainWindow?.show();mainWindow?.focus();mainWindow?.webContents.send('desktop:platform',value);},fetcher:(url,options)=>session.fromPartition('mail-api').fetch(url,options)});
 mailCenter.open();
}
const icon=path.join(__dirname,'icon.ico');
let restartQueue=Promise.resolve();
const configPath=()=>path.join(app.getPath('userData'),'connection.bin');

function loadSettings(){
 if(!existsSync(configPath()))return null;
 if(!safeStorage.isEncryptionAvailable())throw new Error('Windows 本机加密服务不可用，无法读取连接设置。');
 return validateSettings(JSON.parse(safeStorage.decryptString(readFileSync(configPath()))));
}
function saveSettings(settings){
 if(!safeStorage.isEncryptionAvailable())throw new Error('Windows 本机加密服务不可用，配置未保存。');
 const data=safeStorage.encryptString(JSON.stringify(settings));
 mkdirSync(app.getPath('userData'),{recursive:true});
 const temporary=configPath()+'.tmp';writeFileSync(temporary,data,{mode:0o600});renameSync(temporary,configPath());
}
async function stopBackend(){
 const old=backend;backend=null;if(!old)return;
 await new Promise(resolve=>{
  if(old.exitCode!==null || old.signalCode!==null)return resolve();
  let done=false;const finish=()=>{if(done)return;done=true;clearTimeout(timer);resolve();};
  const timer=setTimeout(()=>{old.kill();finish();},4000);
  old.once('exit',finish);
  if(old.connected)old.send({type:'shutdown'});else old.kill();
 });
}
async function startBackend(){
 if(backend){currentOrigin='';resetBackendOrigin();}
 await stopBackend();
 const child=fork(path.join(root,'build','service.cjs'),[],{
  execPath:process.execPath,windowsHide:true,
  env:{...process.env,ELECTRON_RUN_AS_NODE:'1'},
  stdio:['ignore','ignore','ignore','ipc'],
 });
 backend=child;
 return await new Promise((resolve,reject)=>{
  let settled=false;
  const timer=setTimeout(()=>{if(!settled){settled=true;child.kill();reject(new Error('本地接收服务启动超时。'));}},15000);
  child.on('message',message=>{
   if(message?.type==='ready'&&!settled){settled=true;clearTimeout(timer);currentOrigin=message.url;resolveBackendOrigin(currentOrigin);resolve(message);}
   if(message?.type==='fatal'&&!settled){settled=true;clearTimeout(timer);reject(new Error(message.error));}
  });
  child.on('error',()=>{if(!settled){settled=true;clearTimeout(timer);reject(new Error('无法启动内置接收服务。'));}});
  child.on('exit',()=>{
   if(!settled){settled=true;clearTimeout(timer);reject(new Error('本地服务启动失败。'));}
   else if(backend===child&&!quitting){startupError='接收服务已停止，请通过“连接设置”重新启动。';if(mainWindow&&!mainWindow.isDestroyed())mainWindow.webContents.send('desktop:service-stopped');}
  });
  child.send({type:'init',settings:currentSettings,dataDirectory:app.getPath('userData'),accessToken,
   localReaderExecutable:path.join(app.isPackaged?process.resourcesPath:root,'local-reader','WecomLocalReader.exe'),
   wechatReaderExecutable:path.join(app.isPackaged?process.resourcesPath:root,'wechat-reader','WeChatLocalReader.exe')});
 }).catch(error=>{rejectBackendOrigin(error);throw error;});
}
function secureWindow(window,settings=false){
 window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
 window.webContents.on('will-navigate',(event,url)=>{
  if(settings || !url.startsWith(UI_ORIGIN+'/'))event.preventDefault();
 });
 window.webContents.on('will-attach-webview',event=>event.preventDefault());
}
function openSettings(){
 if(settingsWindow&&!settingsWindow.isDestroyed()){settingsWindow.focus();return;}
 settingsWindow=new BrowserWindow({width:730,height:870,minWidth:560,minHeight:620,title:'连接设置 · 企微收件台',icon,
  parent:mainWindow,show:false,backgroundColor:'#f5f8fd',autoHideMenuBar:true,
  webPreferences:{preload:path.join(__dirname,'settings-preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,devTools:!app.isPackaged},
 });
 secureWindow(settingsWindow,true);settingsWindow.once('ready-to-show',()=>settingsWindow.show());settingsWindow.loadFile(settingsPage);
 settingsWindow.on('closed',()=>{settingsWindow=null;});
}
function ensureSettingsSender(event){
 if(!settingsWindow || event.sender!==settingsWindow.webContents || !event.senderFrame.url.startsWith('file:'))throw new Error('无权访问设置');
}
async function restartWeChat(){
 restartQueue=restartQueue.catch(()=>{}).then(async()=>{await startBackend();if(mainWindow&&!mainWindow.isDestroyed())await mainWindow.loadURL(UI_ORIGIN+'/');});
 try{await restartQueue;}catch(error){dialog.showErrorBox('微信接收',error.message);}
}
async function selectWeChat(){
 const result=await dialog.showOpenDialog(mainWindow,{title:'选择微信账号目录（包含 db_storage 的目录）',properties:['openDirectory']});
 if(result.canceled)return;
 const selected=result.filePaths[0];
 if(!existsSync(path.join(selected,'db_storage'))){await dialog.showMessageBox(mainWindow,{type:'error',message:'请选择包含 db_storage 文件夹的微信账号目录。'});return;}
 const directory=path.join(app.getPath('userData'),'wechat-reading');mkdirSync(directory,{recursive:true});
 writeFileSync(path.join(directory,'account-path.txt'),selected,'utf8');
 // Account-scoped filenames must never reuse another account's cached keys.
 require('node:fs').rmSync(path.join(directory,'database-keys.dpapi'),{force:true});
 await restartWeChat();
}
ipcMain.handle('settings:load',event=>{
 ensureSettingsSender(event);
 let settings=currentSettings || {};
 if(!currentSettings&&safeStorage.isEncryptionAvailable()){
  const draftPath=path.join(app.getPath('userData'),'callback-draft.bin');
  try{
   if(existsSync(draftPath))settings=JSON.parse(safeStorage.decryptString(readFileSync(draftPath)));
   else{
    settings={callbackToken:randomBytes(16).toString('hex'),callbackAesKey:randomBytes(32).toString('base64').slice(0,-1),callbackPort:8787,retentionDays:30};
    mkdirSync(app.getPath('userData'),{recursive:true});
    writeFileSync(draftPath,safeStorage.encryptString(JSON.stringify(settings)),{mode:0o600});
   }
  }catch{throw new Error('无法读取或保存本机回调草稿。');}
 }
 return {settings,encryptionAvailable:safeStorage.isEncryptionAvailable(),error:startupError,generatedDraft:!currentSettings};
});
ipcMain.handle('settings:save',async(event,input)=>{
 ensureSettingsSender(event);
 try{
  const validated=validateSettings(input);saveSettings(validated);currentSettings=validated;startupError='';
  restartQueue=restartQueue.catch(()=>{}).then(async()=>{
   const ready=await startBackend();
   if(mainWindow&&!mainWindow.isDestroyed())await mainWindow.loadURL(UI_ORIGIN+'/');
   return {ok:true,receiverError:ready.receiverError || ''};
  });
  return await restartQueue;
 }catch(error){return {ok:false,error:error.message};}
});
ipcMain.on('desktop:settings',event=>{if(mainWindow&&event.sender===mainWindow.webContents)openSettings();});
ipcMain.on('desktop:mail',event=>{if(mainWindow&&event.sender===mainWindow.webContents&&event.senderFrame===mainWindow.webContents.mainFrame)openMailCenter();});
ipcMain.on('desktop:campus',event=>{if(mainWindow&&event.sender===mainWindow.webContents&&event.senderFrame===mainWindow.webContents.mainFrame)openCampusCenter();});
function isAIHotSender(event){return !quitting&&mainWindow&&!mainWindow.isDestroyed()&&event.sender===mainWindow.webContents&&event.senderFrame===event.sender.mainFrame&&event.senderFrame.url===UI_ORIGIN+'/';}
ipcMain.on('desktop:aihot',(event)=>{if(isAIHotSender(event))openAIHotReader();});
ipcMain.on('desktop:aihot-prepare',(event)=>{if(isAIHotSender(event))aiHotReader?.prepare();});
ipcMain.on('desktop:platform',(event,value)=>{if(mainWindow&&event.sender===mainWindow.webContents&&['wechat','wecom'].includes(value))activePlatform=value;});
ipcMain.handle('desktop:article',async(event,value)=>{
 if(!mainWindow||event.sender!==mainWindow.webContents||event.senderFrame!==mainWindow.webContents.mainFrame)throw new Error('无权打开文章');
 const url=new URL(value);if(url.protocol!=='https:'||url.hostname!=='mp.weixin.qq.com'||url.username||url.password||url.port)throw new Error('文章地址无效');
 await shell.openExternal(url.href);return true;
});

app.on('second-instance',()=>{if(mainWindow){if(mainWindow.isMinimized())mainWindow.restore();mainWindow.show();mainWindow.focus();}});
app.whenReady().then(async()=>{
 // The main process owns this fixed loopback transport. Node fetch avoids
 // inheriting renderer-origin/proxy policy in Chromium's network stack.
 protocol.handle('wecom-inbox',createWorkbenchHandler({directory:path.join(root,'build','dist'),backendReady:()=>backendOriginReady,accessToken,fetcher:(url,options)=>fetch(url,options)}));
 const trustedSession=session.defaultSession;
 trustedSession.setPermissionRequestHandler((wc,permission,callback)=>callback(false));
 trustedSession.setPermissionCheckHandler(()=>false);
 trustedSession.webRequest.onBeforeSendHeaders({urls:['http://127.0.0.1:*/*']},(details,callback)=>{
  const headers={...details.requestHeaders};
  if(currentOrigin&&new URL(details.url).origin===currentOrigin)headers['X-Workbench-Key']=accessToken;
  callback({requestHeaders:headers});
 });
 mainWindow=new BrowserWindow({width:1450,height:940,minWidth:920,minHeight:650,title:'收件台',icon,show:false,backgroundColor:'#f5f8fd',
  webPreferences:{preload:path.join(__dirname,'preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,devTools:!app.isPackaged},
 });
 secureWindow(mainWindow);
 campusCenter=require('./campus-desktop.cjs').installCampusCenter({app,BrowserWindow,ipcMain,shell,clipboard,getMainWindow:()=>mainWindow,fetcher:(url,options)=>fetch(url,options)});
 aiHotReader=installAIHotReader({BrowserWindow,WebContentsView,ipcMain,session,getMainWindow:()=>mainWindow,icon});
 let aiHotStartup;
 mainWindow.once('closed',()=>clearTimeout(aiHotStartup));
 mainWindow.once('ready-to-show',()=>{
  mainWindow.show();
  aiHotStartup=setTimeout(()=>{if(!quitting&&!mainWindow.isDestroyed())aiHotReader.prepare();},900);
 });
 const workbenchPage=mainWindow.loadURL(UI_ORIGIN+'/');
 try{currentSettings=loadSettings();}catch(error){startupError=error.message;}
 const changeAttachmentMode = mode => {
  try { setAttachmentMode(app.getPath('userData'), mode); }
  catch { dialog.showErrorBox('附件存储', '无法保存附件设置，请检查本机数据目录。'); }
 };
 const savedMode = attachmentMode(app.getPath('userData'));
 Menu.setApplicationMenu(Menu.buildFromTemplate([
  {label:'应用',submenu:[{label:'邮箱中心',click:openMailCenter},{label:'选择微信账号目录…',click:selectWeChat},{label:'重启微信接收',click:restartWeChat},{label:'官方接入设置…',accelerator:'Ctrl+,',click:openSettings},{label:'打开本机数据目录',click:()=>shell.openPath(app.getPath('userData'))},{type:'separator'},{label:'退出',accelerator:'Alt+F4',click:()=>app.quit()}]},
  {label:'附件存储',submenu:[
   {label:'大附件按需打开（推荐）',type:'radio',checked:savedMode==='on_demand',click:()=>changeAttachmentMode('on_demand')},
   {label:'自动保存可同步的附件',type:'radio',checked:savedMode==='offline',click:()=>changeAttachmentMode('offline')},
  ]},
  {label:'校园资讯',submenu:[{label:'打开校园资讯汇总',click:openCampusCenter}]},
  {label:'AI 热点',submenu:[{label:'打开 AIHot 官网',click:openAIHotReader}]},
  {label:'视图',submenu:[{role:'reload'},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'},{role:'togglefullscreen'}]},
  {label:'帮助',submenu:[{label:'接收范围',click:()=>dialog.showMessageBox(mainWindow,{type:'info',title:'企微收件台',message:'读取本机企业微信消息，不发送消息。',detail:'保持企业微信登录。本机消息数据库发生变化后自动更新，显示客户端已缓存的私聊与群消息。历史记录是否完整取决于本机缓存；图片、语音等以文字占位显示。企业微信升级可能需要重新适配。密钥使用 Windows 当前用户加密保存。'})}]},
 ]));
 let localMode=false;
 try{const ready=await startBackend();await workbenchPage;mainWindow.show();localMode=ready.localMode;startupError=ready.receiverError || startupError;}catch(error){startupError=error.message;dialog.showErrorBox('企微收件台',startupError);mainWindow.show();}
 if((!currentSettings&&!localMode)||startupError)openSettings();
}).catch(error=>{dialog.showErrorBox('启动失败',error.message);app.quit();});
app.on('window-all-closed',()=>app.quit());
app.on('before-quit',event=>{
 if(quitting)return;event.preventDefault();quitting=true;
 aiHotReader?.dispose();
 stopBackend().finally(()=>app.quit());
});
}
