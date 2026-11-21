const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('desktopInbox',Object.freeze({
 openMail:()=>ipcRenderer.send('desktop:mail'),
 openCampus:()=>ipcRenderer.send('desktop:campus'),
 setPlatform:value=>ipcRenderer.send('desktop:platform',value),
 onPlatform:callback=>ipcRenderer.on('desktop:platform',(_,value)=>callback(value)),
 openArticle:url=>ipcRenderer.invoke('desktop:article',url),
 openSettings:()=>ipcRenderer.send('desktop:settings'),
 openAttachment:id=>ipcRenderer.invoke('attachments:open',id),
 saveAttachment:id=>ipcRenderer.invoke('attachments:save-as',id),
 keepAttachment:id=>ipcRenderer.invoke('attachments:keep',id),
 openDocument:url=>ipcRenderer.invoke('documents:open',url),
 openWebLink:url=>ipcRenderer.invoke('links:open',url),
 onServiceStopped:callback=>{if(typeof callback==='function')ipcRenderer.on('desktop:service-stopped',()=>callback());},
}));
window.addEventListener('DOMContentLoaded',()=>{
 const campusButton=document.createElement('button');campusButton.type='button';campusButton.className='source-button';campusButton.textContent='校园资讯';
 campusButton.addEventListener('click',()=>ipcRenderer.send('desktop:campus'));
 const aiHotButton=document.createElement('button');aiHotButton.type='button';aiHotButton.className='source-button';aiHotButton.id='aihot-open';aiHotButton.textContent='AI 热点';aiHotButton.title='在收件台内打开 AIHot 官网';
 let prepareTimer;
 const cancelPrepare=()=>clearTimeout(prepareTimer);
 const prepare=()=>{cancelPrepare();prepareTimer=setTimeout(()=>ipcRenderer.send('desktop:aihot-prepare'),120);};
 for(const type of ['pointerenter','focus'])aiHotButton.addEventListener(type,prepare);
 for(const type of ['pointerleave','blur'])aiHotButton.addEventListener(type,cancelPrepare);
 aiHotButton.addEventListener('click',()=>{cancelPrepare();ipcRenderer.send('desktop:aihot');});
 document.querySelector('.topbar-actions')?.prepend(campusButton,aiHotButton);
 ipcRenderer.on('desktop:service-stopped',()=>{
  const label=document.getElementById('sync-label');if(label)label.textContent='服务已停止，请打开连接设置重启';
 });
});
