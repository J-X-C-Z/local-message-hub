const {contextBridge,ipcRenderer}=require('electron');
const invoke=async(action,...args)=>{const result=await ipcRenderer.invoke('mail:'+action,...args);if(!result.ok)throw new Error(result.error);return result.data;};
contextBridge.exposeInMainWorld('mailCenter',Object.freeze({
 platform:value=>invoke('platform',value),
 status:()=>invoke('status'),configure:(id,input)=>invoke('configure',id,input),importGoogle:()=>invoke('import-google'),
 connect:id=>invoke('connect',id),cancel:id=>invoke('cancel',id),disconnect:id=>invoke('disconnect',id),
 list:(id,folder,cursor)=>invoke('list',id,folder,cursor),get:(id,messageId)=>invoke('get',id,messageId),
 send:(id,requestId,draft)=>invoke('send',id,requestId,draft),openWeb:id=>invoke('open-web',id),
}));
