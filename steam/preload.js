// The bridge between the game page and the Steam window: only these calls, nothing else of Node or Electron
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('mezhaSteam', {
  achieve: id => ipcRenderer.send('mezha:achieve', String(id)),
  quit: () => ipcRenderer.send('mezha:quit'),
  user: () => ipcRenderer.invoke('mezha:user')
});
