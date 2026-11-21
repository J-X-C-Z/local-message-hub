const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('connectionSettings',Object.freeze({load:()=>ipcRenderer.invoke('settings:load'),save:value=>ipcRenderer.invoke('settings:save',value)}));
