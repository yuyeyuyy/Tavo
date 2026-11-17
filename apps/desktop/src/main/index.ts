// Electron 仅管理窗口、原生能力、连接配置与客户端会话。旧实现与学习注释可在 Git 基线 898328d 查看。
import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, Notification, protocol, session, shell, Tray } from 'electron';
import { readFile } from 'node:fs/promises';
import { extname, join, resolve, sep } from 'node:path';
import { z } from 'zod';
import { BackendConnection, backendOrigin } from './backend';
import { externalUrlFromApp } from '../shared/links';

protocol.registerSchemesAsPrivileged([{ scheme: 'tavo', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } }]);
app.setName('Tavo');
app.setPath('userData', process.env.TAVO_USER_DATA ?? join(app.getPath('appData'), 'Tavo'));
let window: BrowserWindow | undefined;
let tray: Tray | undefined;
const rendererDirectory = resolve(__dirname, '../renderer');
const devURL = !app.isPackaged ? process.env.ELECTRON_RENDERER_URL : undefined;
const trustedURL = devURL ?? 'tavo://app/index.html';
const backend = new BackendConnection(join(app.getPath('userData'), 'client-profile.json'), process.env.TAVO_BACKEND_URL ?? 'http://127.0.0.1:8000', process.env.TAVO_CLIENT_TOKEN_FILE ?? (!app.isPackaged ? resolve(app.getAppPath(), '../../backend/.local/client.token') : undefined));

function csp() {
  const development = !!devURL;
  return `default-src 'self'; script-src 'self'${development ? " 'unsafe-inline'" : ''}; style-src 'self'${development ? " 'unsafe-inline'" : ''}; img-src 'self' data:; connect-src 'self' ${backend.profile().backendUrl}${development ? ' ws://localhost:* ws://127.0.0.1:*' : ''}; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'`;
}
function createWindow() {
  window = new BrowserWindow({ width: 1180, height: 820, minWidth: 620, minHeight: 540, title: 'Tavo', backgroundColor: '#111214', titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 20, y: 25 }, show: !backend.profile().startMinimized,
    webPreferences: { preload: join(__dirname, '../preload/index.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true },
  });
  window.webContents.setWindowOpenHandler(details => {
    if (details.postBody) return { action: 'deny' };
    const url = externalUrlFromApp(window?.webContents.getURL() ?? '', trustedURL, details.url);
    if (url) void shell.openExternal(url).catch(() => { dialog.showErrorBox('无法打开链接', '请复制链接后在浏览器中打开。'); });
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event) => event.preventDefault());
  window.webContents.on('will-attach-webview', (event) => event.preventDefault());
  window.on('closed', () => { window = undefined; });
  void window.loadURL(trustedURL);
}
function assertSender(event: Electron.IpcMainInvokeEvent) {
  const actual = event.senderFrame?.url;
  if (event.sender !== window?.webContents || event.senderFrame !== event.sender.mainFrame || !actual || new URL(actual).href.split('#')[0] !== new URL(trustedURL).href.split('#')[0]) throw new Error('FORBIDDEN');
}
async function start() {
  await app.whenReady(); await backend.load();
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  backend.attach(session.defaultSession, () => window?.webContents.id);
  if (devURL) session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = { ...details.responseHeaders };
    if (new URL(details.url).origin === new URL(devURL).origin) headers['Content-Security-Policy'] = [csp()];
    callback({ responseHeaders: headers });
  });
  protocol.handle('tavo', async (request) => {
    try {
      const url = new URL(request.url); if (url.host !== 'app') return new Response('Not found', { status: 404 });
      const path = resolve(rendererDirectory, '.' + (url.pathname === '/' ? '/index.html' : decodeURIComponent(url.pathname)));
      if (!path.startsWith(rendererDirectory + sep)) return new Response('Forbidden', { status: 403 });
      const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[extname(path)] ?? 'application/octet-stream';
      return new Response(new Uint8Array(await readFile(path)), { headers: { 'Content-Type': type, 'Content-Security-Policy': csp(), 'X-Content-Type-Options': 'nosniff' } });
    } catch { return new Response('Not found', { status: 404 }); }
  });
  ipcMain.handle('desktop:profile', (event) => { assertSender(event); return backend.profile(); });
  ipcMain.handle('desktop:reconnect', (event) => { assertSender(event); return backend.reconnect(); });
  ipcMain.handle('desktop:preferences', async (event, input: unknown) => { assertSender(event); const value = z.strictObject({ startMinimized: z.boolean() }).parse(input); return backend.preferences(value.startMinimized); });
  ipcMain.handle('desktop:configure', async (event, input: unknown) => {
    assertSender(event);
    try {
      const value = z.strictObject({ backendUrl: z.string().url() }).parse(input); const origin = backendOrigin(value.backendUrl);
      const approved = await dialog.showMessageBox(window!, { type: 'question', title: '配置后端连接', message: `连接到 ${origin}`, detail: '请选择此后端专用的客户端凭据文件。现有凭据不会自动发送到新地址。', buttons: ['选择凭据文件', '取消'], cancelId: 1, defaultId: 1 });
      if (approved.response !== 0) return { ok: false, message: '已取消配置。' };
      const selected = await dialog.showOpenDialog(window!, { title: '选择客户端凭据文件', properties: ['openFile'] });
      if (selected.canceled || !selected.filePaths[0]) return { ok: false, message: '已取消配置。' };
      await backend.configure(origin, selected.filePaths[0]); window?.reload(); return { ok: true };
    } catch { return { ok: false, message: '后端配置失败。远程地址需 HTTPS，凭据文件需具有私有权限。' }; }
  });
  ipcMain.handle('desktop:selectFile', async (event) => { assertSender(event); const result = await dialog.showOpenDialog(window!, { properties: ['openFile'] }); return result.canceled ? null : result.filePaths[0] ?? null; });
  ipcMain.handle('desktop:notify', (event, input: unknown) => { assertSender(event); const value = z.strictObject({ title: z.string().min(1).max(80), body: z.string().max(200) }).parse(input); if (!Notification.isSupported()) return false; new Notification(value).show(); return true; });
  const icon = nativeImage.createFromDataURL('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==').resize({ width: 16, height: 16 });
  icon.setTemplateImage(true); tray = new Tray(icon); tray.setToolTip('Tavo'); tray.setContextMenu(Menu.buildFromTemplate([{ label: '显示 Tavo', click: () => { if (!window) createWindow(); else window.show(); } }, { label: '退出', click: () => app.quit() }]));
  createWindow(); app.on('activate', () => { if (!window) createWindow(); else window.show(); });
}
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('before-quit', () => { tray?.destroy(); });
if (!app.requestSingleInstanceLock()) app.exit(0);
else { app.on('second-instance', () => window?.show()); void start().catch(() => { dialog.showErrorBox('Tavo 启动失败', '桌面配置未能加载，请检查本地配置。'); app.exit(1); }); }
