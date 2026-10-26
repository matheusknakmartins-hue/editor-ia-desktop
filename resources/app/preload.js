const {contextBridge, ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('editor',{
  call:(name,data)=>ipcRenderer.invoke('editor',name,data),
  updates:callback=>ipcRenderer.on('state',(_,state)=>callback(state))
});
