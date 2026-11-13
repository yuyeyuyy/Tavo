// electron关键组件
import { app, BrowserWindow, dialog, ipcMain, session, safeStorage } from 'electron';
/***

 **/
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
/***
core: 业务层
connectors: 第三方API客户端
agent: 智能体
 storage: DAO 面向数据库连接层
 shared: 公共模块，相当于common，里边有一些数据转换DTO和校验contracts之类的
 **/
import { ApplicationRuntime, EncryptedCredentialStore, EventBus, WorkspaceRuntime, type WorkspaceRuntimeEvents } from '@tavo/core';
import { MockConnector } from '@tavo/connectors';
import { MockAgentRuntime } from '@tavo/agent';
import { SqliteStorage } from '@tavo/storage';
import { publicError, workspaceRequestSchemas, type WorkspaceOperation } from '@tavo/shared';
// ipc 相当于前后端的通讯层
import { dispatchWorkspaceRequest } from './ipc';
import { ElectronEncryptionProvider } from './security';



// 设置应用名称
app.setName('Tavo');
// 仅供本地自动化测试隔离数据库；不通过 IPC 开放文件路径。
app.setPath('userData', process.env.TAVO_USER_DATA ?? join(app.getPath('appData'), 'Tavo'));
let runtime: ApplicationRuntime | undefined;
let workspace: WorkspaceRuntime | undefined;
let window: BrowserWindow | undefined;
let shuttingDown = false;
const rendererFile = join(__dirname, '../renderer/index.html');
const trustedURL = !app.isPackaged && process.env.ELECTRON_RENDERER_URL
  ? process.env.ELECTRON_RENDERER_URL : pathToFileURL(rendererFile).href;

function createWindow(): void {
  window = new BrowserWindow({
    width: 1180, height: 820, minWidth: 880, minHeight: 650,
    title: 'Tavo', backgroundColor: '#f7f7f7',
    show: !workspace?.snapshot().settings.startMinimized,
    webPreferences: {
      preload: join(__dirname, '../preload/index.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event) => { event.preventDefault(); });
  window.webContents.on('will-attach-webview', (event) => { event.preventDefault(); });
  window.on('closed', () => { window = undefined; });
  void (trustedURL.startsWith('file:') ? window.loadFile(rendererFile) : window.loadURL(trustedURL));
}

async function start(): Promise<void> {
  await app.whenReady();
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  const storage = new SqliteStorage(join(app.getPath('userData'), 'tavo.sqlite'));
  const agent = new MockAgentRuntime();
  runtime = await ApplicationRuntime.create(storage, agent, new MockConnector());
  const credentials = new EncryptedCredentialStore(new ElectronEncryptionProvider(safeStorage), storage.workspace.ciphertexts);
  const security = { ...await credentials.status(), dataLocation: app.getPath('userData') };
  const events = new EventBus<WorkspaceRuntimeEvents>();
  workspace = new WorkspaceRuntime(runtime, storage.workspace, runtime.connectors, credentials, agent, security, {
    publish: (persisted) => { for (const event of persisted) events.emit('business.persisted', event); },
  });
  await workspace.initialize();
  for (const operation of ['snapshot', 'analyze', ...Object.keys(workspaceRequestSchemas)] as (WorkspaceOperation | 'snapshot' | 'analyze')[]) {
    ipcMain.handle(`tavo:${operation}`, (event, input: unknown) => {
      if (!workspace || shuttingDown) return { ok: false, error: publicError(undefined) };
      return dispatchWorkspaceRequest(workspace, operation, input, {
        url: event.senderFrame?.url ?? '',
        isMainFrame: event.senderFrame !== null && event.senderFrame === event.sender.mainFrame,
        isOwner: event.sender === window?.webContents,
      }, trustedURL);
    });
  }
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); else window?.show(); });
}

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('before-quit', (event) => {
  if (shuttingDown) return;
  event.preventDefault(); shuttingDown = true;
  // 正常退出取消活动请求；崩溃遗留任务由独立同步引擎恢复。
  void (async () => { try { await workspace?.close(); } finally { await runtime?.close(); } })().catch(() => {}).finally(() => app.quit());
});
if (!app.requestSingleInstanceLock()) app.exit(0);
else {
  app.on('second-instance', () => { window?.show(); window?.focus(); });
  void start().catch((error: unknown) => {
    dialog.showErrorBox('Tavo 启动失败', publicError(error).message);
    app.exit(1);
  });
}
