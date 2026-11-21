(() => {
 const pending=new Map();
 let sequence=0;
 const call=(action,...args)=>new Promise((resolve,reject)=>{
  const id=++sequence;
  const timer=setTimeout(()=>{pending.delete(id);reject(new Error('本机操作未完成，请重试'));},190000);
  pending.set(id,{resolve,reject,timer});
  window.ipc.postMessage(JSON.stringify({id,action,args}));
 });
 const send=(action,...args)=>window.ipc.postMessage(JSON.stringify({id:0,action,args}));
 window.__inboxReply=(id,value,error)=>{
  const entry=pending.get(id);if(!entry)return;pending.delete(id);clearTimeout(entry.timer);
  if(error)entry.reject(new Error(error));else entry.resolve(value);
 };
 window.__inboxEvent=(name,detail)=>window.dispatchEvent(new CustomEvent(name,{detail}));
 const actions=(prefix,names)=>Object.fromEntries(names.map(name=>[name,(...args)=>call(prefix+name,...args)]));
 if(location.pathname==='/'){
  window.desktopInbox=Object.freeze({openMail:()=>send('show:mail'),openCampus:()=>send('show:campus'),openSettings:()=>send('show:settings'),
   setPlatform:()=>{},onPlatform:fn=>window.addEventListener('inbox-platform',event=>fn(event.detail)),
   openArticle:url=>call('links:open',url),openAttachment:id=>call('attachments:open',id),saveAttachment:id=>call('attachments:save-as',id),
   keepAttachment:id=>call('attachments:keep',id),openDocument:url=>call('documents:open',url),openWebLink:url=>call('links:open',url)});
  window.addEventListener('DOMContentLoaded',()=>{
   const bar=document.querySelector('.topbar-actions');
   for(const [label,action] of [['校园资讯','show:campus'],['AI 热点','show:aihot']]){
    const button=document.createElement('button');button.type='button';button.className='source-button';button.textContent=label;
    if(action==='show:aihot')button.id='aihot-open';
    button.onclick=()=>send(action);bar?.append(button);
   }
   send('main:ready');
  });
  window.addEventListener('inbox-service-failed',()=>{const label=document.getElementById('sync-label');if(label)label.textContent='连接未完成，可在接收设置中重启';});
 }
 if(location.pathname==='/desktop/settings.html'){
  window.connectionSettings={...actions('settings:',['load','save']),close:()=>send('window:close'),
   chooseWechat:()=>call('settings:select-wechat'),restart:()=>send('service:restart'),
   storageMode:()=>call('settings:attachment-mode'),setStorageMode:mode=>call('settings:set-attachment-mode',mode)};
  window.addEventListener('DOMContentLoaded',()=>{
   document.getElementById('close-button').addEventListener('click',()=>window.connectionSettings.close());
   const notice=document.createElement('p');notice.setAttribute('role','status');
   const row=document.createElement('div');row.className='actions';
   for(const [name,fn] of [['选择微信账号目录',async()=>{const value=await window.connectionSettings.chooseWechat();if(!value.canceled)notice.textContent='账号目录已设置，正重新连接。';}],['重启消息接收',()=>{window.connectionSettings.restart();notice.textContent='正在重新连接消息接收。';}],['切换附件保存方式',async()=>{const mode=await window.connectionSettings.storageMode();await window.connectionSettings.setStorageMode(mode==='offline'?'on_demand':'offline');notice.textContent=mode==='offline'?'已改为大附件按需打开。':'已改为自动保存可同步的附件。';}]]){
    const button=document.createElement('button');button.type='button';button.textContent=name;
    button.onclick=async()=>{try{await fn();}catch(error){notice.textContent=error.message;}};row.append(button);
   }
   document.querySelector('main').append(row,notice);
  });
 }
 if(location.pathname==='/desktop/campus.html')window.campus={...actions('campus:',['snapshot','refresh','read','detail','open','copy','setSource','addSource','removeSource']),onUpdate:fn=>window.addEventListener('campus-updated',fn)};
 if(location.pathname==='/desktop/mail.html'){
  const invoke=async(name,...args)=>{const value=await call('mail:'+name,...args);if(!value.ok)throw new Error(value.error);return value.data;};
  window.mailCenter=Object.fromEntries(Object.entries({status:'status',configure:'configure',importGoogle:'import-google',connect:'connect',cancel:'cancel',disconnect:'disconnect',list:'list',get:'get',send:'send',openWeb:'open-web',platform:'platform'}).map(([name,action])=>[name,(...args)=>invoke(action,...args)]));
 }
 if(location.pathname==='/desktop/aihot.html')window.aiHotReader={command:action=>send('aihot:'+action),onState:fn=>window.addEventListener('aihot-state',event=>fn(event.detail))};
})();
