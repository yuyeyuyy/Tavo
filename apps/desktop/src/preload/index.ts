import { contextBridge, ipcRenderer } from 'electron';

const desktop = {
  profile: () => ipcRenderer.invoke('desktop:profile'),
  reconnect: () => ipcRenderer.invoke('desktop:reconnect'),
  configure: (input: { backendUrl: string }) => ipcRenderer.invoke('desktop:configure', input),
  preferences: (input: { startMinimized: boolean }) => ipcRenderer.invoke('desktop:preferences', input),
  selectFile: () => ipcRenderer.invoke('desktop:selectFile'),
  notify: (input: { title: string; body: string }) => ipcRenderer.invoke('desktop:notify', input),
};
contextBridge.exposeInMainWorld('desktop', Object.freeze(desktop));
