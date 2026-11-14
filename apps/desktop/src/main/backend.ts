import { readFile, open, writeFile, mkdir } from 'node:fs/promises';
import { constants } from 'node:fs';
import { dirname } from 'node:path';
import { z } from 'zod';
import type { Session } from 'electron';

const savedSchema = z.strictObject({ backendUrl: z.string().url(), tokenFile: z.string().optional(), startMinimized: z.boolean() });
const sessionSchema = z.object({ accessToken: z.string().min(32).max(2048), expiresAt: z.string().datetime() });
export function backendOrigin(value: string): string {
  const url = new URL(value);
  if (url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) throw new Error('INVALID_PROFILE');
  const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) throw new Error('HTTPS_REQUIRED');
  return url.origin;
}
export class BackendConnection {
  private configuration: z.infer<typeof savedSchema>;
  private session?: { token: string; expires: number };
  private pending?: Promise<string>;
  private generation = 0;
  constructor(private readonly file: string, initialUrl: string, tokenFile?: string) {
    this.configuration = { backendUrl: backendOrigin(initialUrl), tokenFile, startMinimized: false };
  }
  async load() {
    try { const value = savedSchema.parse(JSON.parse(await readFile(this.file, 'utf8'))); this.configuration = { ...value, backendUrl: backendOrigin(value.backendUrl) }; }
    catch { /* First launch or invalid native preferences: keep validated defaults. */ }
  }
  profile() { return { backendUrl: this.configuration.backendUrl, authenticated: !!this.session && this.session.expires > Date.now(), startMinimized: this.configuration.startMinimized }; }
  async configure(url: string, tokenFile: string) {
    const previous = { ...this.configuration };
    this.configuration = { ...this.configuration, backendUrl: backendOrigin(url), tokenFile };
    this.generation++; this.session = undefined; this.pending = undefined;
    try { await this.authenticate(); await this.save(); return this.profile(); }
    catch { this.configuration = previous; this.generation++; this.session = undefined; this.pending = undefined; throw new Error('CONFIGURATION_FAILED'); }
  }
  async preferences(startMinimized: boolean) { this.configuration.startMinimized = startMinimized; await this.save(); return this.profile(); }
  private async save() { await mkdir(dirname(this.file), { recursive: true, mode: 0o700 }); await writeFile(this.file, JSON.stringify(this.configuration), { mode: 0o600 }); }
  private async ownerToken() {
    const file = this.configuration.tokenFile;
    if (!file) throw new Error('AUTH_FILE_REQUIRED');
    const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    try {
      const metadata = await handle.stat();
      if (!metadata.isFile() || metadata.size > 4096 || (process.platform !== 'win32' && (metadata.mode & 0o077) !== 0)) throw new Error('PRIVATE_AUTH_FILE_REQUIRED');
      const value = (await handle.readFile('utf8')).trim(); if (value.length < 32 || value.length > 2048) throw new Error('INVALID_AUTH_FILE'); return value;
    } finally { await handle.close(); }
  }
  async authenticate(): Promise<string> {
    if (this.session && this.session.expires > Date.now() + 30_000) return this.session.token;
    if (this.pending) return this.pending;
    const generation = this.generation; const origin = this.configuration.backendUrl;
    const request = (async () => {
      const response = await fetch(`${origin}/api/v1/auth/session`, { method: 'POST', headers: { Authorization: `Bearer ${await this.ownerToken()}` }, redirect: 'error', signal: AbortSignal.timeout(3000) });
      if (!response.ok) throw new Error('SESSION_REJECTED');
      const value = sessionSchema.parse(await response.json());
      if (generation !== this.generation || origin !== this.configuration.backendUrl) throw new Error('PROFILE_CHANGED');
      this.session = { token: value.accessToken, expires: Date.parse(value.expiresAt) }; return value.accessToken;
    })();
    this.pending = request;
    try { return await request; } finally { if (this.pending === request) this.pending = undefined; }
  }
  async reconnect() { this.session = undefined; try { await this.authenticate(); return { ok: true as const, profile: this.profile() }; } catch { return { ok: false as const, message: '无法连接或认证后端，请检查地址、服务和客户端凭据文件。' }; } }
  attach(session: Session, trustedWindowId: () => number | undefined) {
    session.webRequest.onBeforeSendHeaders(async (details, callback) => {
      const headers = { ...details.requestHeaders };
      if (details.webContentsId !== trustedWindowId()) { callback({ requestHeaders: headers }); return; }
      // Native session credentials never enter Renderer. Strip on redirects/other origins.
      for (const name of Object.keys(headers)) if (['authorization', 'proxy-authorization'].includes(name.toLowerCase())) delete headers[name];
      const url = new URL(details.url); const origin = this.configuration.backendUrl;
      if (url.origin === origin && url.pathname.startsWith('/api/v1/') && details.method !== 'OPTIONS') {
        try { const token = await this.authenticate(); if (url.origin === this.configuration.backendUrl) headers.Authorization = `Bearer ${token}`; }
        catch { /* Server returns a controlled auth error; never expose native errors or tokens. */ }
      }
      callback({ requestHeaders: headers });
    });
  }
}
