const {contextBridge,ipcRenderer}=require('electron');

contextBridge.exposeInMainWorld('aiHotReader',Object.freeze({
 command:action=>ipcRenderer.send('aihot:command',action),
 onState:callback=>{if(typeof callback==='function')ipcRenderer.on('aihot:state',(_event,state)=>callback(state));},
}));
