const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('desktop', Object.freeze({
  getInfo: () => ipcRenderer.invoke('desktop:info'),
  getConnection: () => ipcRenderer.invoke('desktop:connection'),
  saveConnection: (settings) => ipcRenderer.invoke('desktop:save-connection', settings),
  restoreBackup: () => ipcRenderer.invoke('desktop:restore-backup'),
  openData: () => ipcRenderer.invoke('desktop:open-data'),
  openLogs: () => ipcRenderer.invoke('desktop:open-logs'),
}));
