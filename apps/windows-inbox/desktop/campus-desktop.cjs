const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {DatabaseSync}=require('node:sqlite');
const {CampusMonitor,wechatKey}=require('../build/campus.cjs');
function installCampusCenter({app,BrowserWindow,ipcMain,shell,clipboard=require('electron').clipboard,getMainWindow,fetcher=fetch}){
 let window=null;
 const page=path.join(__dirname,'campus.html'),pageUrl=pathToFileURL(page).href;
 const monitor=new CampusMonitor({directory:app.getPath('userData'),fetcher,onUpdate:()=>{if(window&&!window.isDestroyed())window.webContents.send('campus:updated');},readWeChat:accounts=>{
  let db;try{
   db=new DatabaseSync(path.join(app.getPath('userData'),'messages.db'),{readOnly:true});db.exec('PRAGMA busy_timeout=250');db.function('campus_account',{deterministic:true},wechatKey);
   const names=accounts.map(wechatKey);
   return db.prepare(`SELECT payload FROM events WHERE received_at > ? AND dedup_key LIKE 'wechat:%' AND json_valid(payload) AND json_extract(payload,'$.ConversationType') IN ('official','service') AND campus_account(json_extract(payload,'$.ConversationName')) IN (${names.map(()=>'?').join(',')}) AND payload LIKE '%mp.weixin.qq.com%' ORDER BY received_at DESC LIMIT 3000`).all(Date.now()-180*86400000,...names).map(row=>JSON.parse(row.payload));
  }finally{db?.close();}
 }});
 function ensure(event){if(!window||event.sender!==window.webContents||event.senderFrame!==window.webContents.mainFrame||event.senderFrame.url!==pageUrl)throw new Error('无权访问校园资讯');}
 for(const [action,handler] of Object.entries({snapshot:()=>monitor.snapshot(),refresh:async()=>{await monitor.refresh();return monitor.snapshot();},read:ids=>monitor.markRead(ids),detail:id=>monitor.detail(id),setSource:(id,enabled)=>monitor.setSource(id,enabled),addSource:(type,name,url)=>monitor.addSource(type,name,url),removeSource:id=>monitor.removeSource(id),open:async url=>{await shell.openExternal(monitor.openAddress(url));return true;},copy:url=>{clipboard.writeText(monitor.openAddress(url));return true;}})){
  ipcMain.handle('campus:'+action,async(event,...args)=>{try{ensure(event);return {ok:true,data:await handler(...args)};}catch(error){return {ok:false,error:error.message};}});
 }
 function open(){if(window&&!window.isDestroyed()){if(window.isMinimized())window.restore();window.show();window.focus();return;}
  window=new BrowserWindow({width:1380,height:920,minWidth:960,minHeight:660,title:'校园资讯 · 中山大学优先',parent:getMainWindow(),show:false,autoHideMenuBar:true,backgroundColor:'#f3f6fa',webPreferences:{preload:path.join(__dirname,'campus-preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,devTools:!app.isPackaged}});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));window.webContents.on('will-navigate',event=>event.preventDefault());window.webContents.on('will-attach-webview',event=>event.preventDefault());
  window.once('ready-to-show',()=>window?.show());window.on('closed',()=>window=null);window.loadFile(page);
 }
 monitor.start();app.on('before-quit',()=>monitor.stop());return {open,monitor};
}
module.exports={installCampusCenter};
