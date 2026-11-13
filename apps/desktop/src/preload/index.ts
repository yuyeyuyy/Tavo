import { contextBridge, ipcRenderer } from 'electron';
import { responseSchema, type WorkspaceAPI, type WorkspaceOperation, type Response } from '@tavo/shared';

async function invoke(operation: WorkspaceOperation | 'snapshot' | 'analyze', input: unknown): Promise<Response> {
  return responseSchema.parse(await ipcRenderer.invoke(`tavo:${operation}`, input));
}
const api: WorkspaceAPI = {
  snapshot: () => invoke('snapshot', {}),
  analyze: (input) => invoke('analyze', input),
  addConnection: (input) => invoke('addConnection', input),
  disconnect: (input) => invoke('disconnect', input), removeConnection: (input) => invoke('removeConnection', input),
  reconnect: (input) => invoke('reconnect', input), syncConnection: (input) => invoke('syncConnection', input),
  cancelSync: (input) => invoke('cancelSync', input), configureMock: (input) => invoke('configureMock', input),
  updateSettings: (input) => invoke('updateSettings', input),
};
contextBridge.exposeInMainWorld('tavo', Object.freeze(api));
