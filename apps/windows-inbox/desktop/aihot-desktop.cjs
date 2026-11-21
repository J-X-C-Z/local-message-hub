const path=require('node:path');
const {isIPv4,isIPv6}=require('node:net');
const {pathToFileURL}=require('node:url');

const AIHOT_HOME='https://aihot.news/';
const AIHOT_ORIGIN=new URL(AIHOT_HOME).origin;
const AIHOT_PARTITION='persist:aihot-reader';
const TOOLBAR_HEIGHT=62;
const READER_TTL=15*60*1000;
const toolbarPath=path.join(__dirname,'aihot.html');
const toolbarUrl=pathToFileURL(toolbarPath).href;

function readerAddress(value){
 if(typeof value!=='string')return null;
 try{
  const url=new URL(value);
  const host=url.hostname.toLowerCase().replace(/\.$/,'');
  if(url.protocol!=='https:'||url.username||url.password||url.port||!host.includes('.')||
   isIPv4(host)||isIPv6(host.replace(/^\[|\]$/g,''))||/(?:^|\.)(?:localhost|local|internal|lan|home|test|invalid)$/.test(host))return null;
  return url;
 }catch{return null;}
}

function installAIHotReader({BrowserWindow,WebContentsView,ipcMain,session,getMainWindow,icon,timeoutMs=18000,retentionMs=READER_TTL}){
 let active,partition;
 function readerSession(){
  if(partition)return partition;
  partition=session.fromPartition(AIHOT_PARTITION);
  partition.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
  partition.setPermissionCheckHandler(()=>false);
  partition.on('will-download',(event,_item,contents)=>{
   event.preventDefault();
   if(active?.contents===contents)active.notice('此链接是下载文件，无法直接在阅读页显示。',true);
  });
  partition.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(details,callback)=>{
   if(details.resourceType==='mainFrame'&&!readerAddress(details.url))return callback({cancel:true});
   if(!readerAddress(details.url)&&!readerAddress(details.url.replace(/^http:/,'https:')))return callback({cancel:true});
   callback({cancel:false});
  });
  return partition;
 }
 ipcMain.on('aihot:command',(event,command)=>{
  if(!active||event.sender!==active.window.webContents||event.senderFrame!==event.sender.mainFrame||event.senderFrame.url!==toolbarUrl)return;
  active.command(command);
 });
 function create(){
  const parent=getMainWindow();
  const window=new BrowserWindow({width:1310,height:900,minWidth:740,minHeight:500,parent,icon,
   title:'AI 热点 · 企微收件台',show:false,autoHideMenuBar:true,backgroundColor:'#f5f7fb',
   webPreferences:{preload:path.join(__dirname,'aihot-preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,devTools:false}});
  window.removeMenu();
  const view=new WebContentsView({webPreferences:{session:readerSession(),preload:path.join(__dirname,'aihot-content-preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,
   nodeIntegrationInSubFrames:false,nodeIntegrationInWorker:false,webviewTag:false,webSecurity:true,devTools:false}});
  const contents=view.webContents;
  const history=contents.navigationHistory;
  const chrome=window.webContents;
  let lastRequest=AIHOT_HOME,timer,expiry,slowTimer,ready=false,presented=false,closed=false,loading=false,failed=false,message='',responseNotice='';
  window.contentView.addChildView(view);
  function resize(){
   const [width,height]=window.getContentSize();
   view.setBounds({x:0,y:TOOLBAR_HEIGHT,width,height:Math.max(0,height-TOOLBAR_HEIGHT)});
  }
  function publish(){
   if(closed||window.isDestroyed())return;
   const displaying=Boolean(readerAddress(contents.getURL()));
   view.setVisible(!failed&&displaying);
   if(ready)chrome.send('aihot:state',{loading,failed,message,waiting:!failed&&!displaying,host:readerAddress(loading||failed?lastRequest:contents.getURL())?.hostname??'aihot.news',
    canBack:history.canGoBack()||(failed&&Boolean(readerAddress(contents.getURL()))&&contents.getURL()!==lastRequest),canForward:history.canGoForward()});
  }
  function notice(text,finish=false){
   if(closed)return;
   if(finish){clearTimers();loading=false;failed=!readerAddress(contents.getURL());}
   message=text;publish();
  }
  function fail(text){
   if(closed)return;
   clearTimers();loading=false;failed=true;message=text;publish();
  }
  function clearTimers(){
   clearTimeout(timer);clearTimeout(slowTimer);
  }
  function start(url){
   clearTimers();lastRequest=url;loading=true;failed=false;message='';responseNotice='';publish();
   slowTimer=setTimeout(()=>{message='网站响应较慢，可返回继续阅读。';publish();},1200);
   timer=setTimeout(()=>{contents.stop();fail('页面响应较慢。可以重试，或返回继续阅读。');},timeoutMs);
  }
  function finish(){
   if(failed||!readerAddress(contents.getURL()))return;
   clearTimers();loading=false;message=responseNotice;publish();
  }
  function navigate(value){
   const url=readerAddress(value);
   if(!url){notice('此链接不支持在阅读页打开；没有跳转到外部应用。');return;}
   void contents.loadURL(url.href).catch(()=>{});
  }
  function command(action){
   if(closed)return;
   if(action==='close'){hideReader();return;}
   if(action==='home'){
    const index=history.getAllEntries().findIndex(entry=>entry.url===AIHOT_HOME);
    if(index>=0&&index!==history.getActiveIndex())history.goToIndex(index);
    else if(failed||contents.getURL()!==AIHOT_HOME)navigate(AIHOT_HOME);
   }else if(action==='back'){
    if(failed&&readerAddress(contents.getURL())&&contents.getURL()!==lastRequest){failed=false;finish();contents.focus();}
    else if(history.canGoBack())history.goBack();
   }else if(action==='forward'&&history.canGoForward())history.goForward();
   else if(action==='reload'){
    if(failed||!readerAddress(contents.getURL()))navigate(lastRequest);
    else contents.reload();
   }
  }
  function onParentClosed(){destroy();}
  function destroy(){
   if(!window.isDestroyed())window.destroy();
  }
  function hideReader(){
   if(readerAddress(contents.getURL())?.origin!==AIHOT_ORIGIN||readerAddress(lastRequest)?.origin!==AIHOT_ORIGIN){
    destroy();return;
   }
   presented=false;contents.setAudioMuted(true);window.hide();
   clearTimeout(expiry);expiry=setTimeout(destroy,retentionMs);
  }
  function show(){
   clearTimeout(expiry);
   presented=true;
   if(window.isMinimized())window.restore();
   contents.setAudioMuted(false);
   if(failed&&!readerAddress(contents.getURL()))navigate(AIHOT_HOME);
   window.show();window.focus();
  }
  function guardNavigation(event){
   if(event.isMainFrame&&!readerAddress(event.url)){
    event.preventDefault();
    if(loading)fail('该网页尝试跳转到不支持的地址。');
    else notice('此链接不支持在阅读页打开；没有跳转到外部应用。');
   }
   else if(event.isMainFrame&&!presented&&readerAddress(event.url)?.origin!==AIHOT_ORIGIN)event.preventDefault();
  }
  function shortcuts(event,input){
   if(input.type!=='keyDown')return;
   const action=input.alt&&(input.key==='ArrowLeft'||input.key==='ArrowRight')?(input.key==='ArrowLeft'?'back':'forward'):
    input.key==='F5'||(input.control&&input.key.toLowerCase()==='r')?'reload':null;
   if(action){event.preventDefault();command(action);}
  }
  active={window,contents,command,notice,show,destroy};
  expiry=setTimeout(destroy,retentionMs);
  contents.setAudioMuted(true);
  parent?.once('closed',onParentClosed);
  resize();window.on('resize',resize);
  chrome.setWindowOpenHandler(()=>({action:'deny'}));
  chrome.on('will-navigate',event=>event.preventDefault());
  chrome.on('before-input-event',shortcuts);
  chrome.once('did-finish-load',()=>{ready=true;publish();});
  window.once('ready-to-show',()=>{if(presented&&!window.isVisible())window.show();});
  window.on('close',event=>{event.preventDefault();hideReader();});
  window.on('closed',()=>{
   closed=true;clearTimers();clearTimeout(expiry);active=null;
   parent?.removeListener('closed',onParentClosed);
   if(!contents.isDestroyed())contents.close({waitForBeforeUnload:false});
  });
  contents.on('will-attach-webview',event=>event.preventDefault());
  contents.on('will-frame-navigate',guardNavigation);
  contents.on('will-redirect',event=>{
   guardNavigation(event);
   if(event.isMainFrame&&!event.defaultPrevented){lastRequest=event.url;publish();}
  });
  contents.setWindowOpenHandler(({url,postBody})=>{
   queueMicrotask(()=>{if(!closed&&presented){if(postBody)notice('此网页不支持在阅读页打开提交表单的新窗口。');else navigate(url);}});
   return {action:'deny'};
  });
  contents.on('before-input-event',shortcuts);
  contents.on('did-start-navigation',event=>{if(event.isMainFrame&&!event.isSameDocument)start(event.url);});
  contents.on('did-navigate',(_event,url,status)=>{
   if(status>=400)responseNotice=`来源网站返回 HTTP ${status}。可重试或返回 AIHot。`;
   if(readerAddress(url)){lastRequest=url;publish();}
  });
  contents.on('did-navigate-in-page',(_event,url,isMainFrame)=>{
   if(isMainFrame){lastRequest=url;failed=false;responseNotice='';finish();}
  });
  contents.on('dom-ready',finish);
  contents.on('did-finish-load',finish);
  contents.on('did-fail-load',(_event,code,_description,url,isMainFrame)=>{
   if(isMainFrame&&code!==-3&&(!url||url===lastRequest))fail('无法加载此网页。请检查网络，或返回继续阅读。');
  });
  contents.on('render-process-gone',()=>fail('阅读页已停止响应，请重试。'));
  void window.loadFile(toolbarPath).catch(destroy);
  navigate(AIHOT_HOME);
  return active;
 }
 function prepare(){if(!active)create();}
 function open(){(active??create())?.show();}
 function dispose(){active?.destroy();}
 return {open,prepare,dispose};
}

module.exports={AIHOT_HOME,AIHOT_PARTITION,installAIHotReader,readerAddress};
