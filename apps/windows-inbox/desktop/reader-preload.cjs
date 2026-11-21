const{contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('windowReader',Object.freeze({state:()=>ipcRenderer.invoke('reader:state'),start:seconds=>ipcRenderer.invoke('reader:start',seconds),stop:()=>ipcRenderer.invoke('reader:stop'),read:()=>ipcRenderer.invoke('reader:read'),openOfficial:()=>ipcRenderer.send('reader:official')}));
