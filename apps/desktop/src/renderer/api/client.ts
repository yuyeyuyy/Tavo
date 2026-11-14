import { z } from 'zod';
import { AppError, analysisSchema, businessEventSchema, errorCodeSchema, publicError, snapshotSchema, type BusinessEvent, type Response, type Snapshot, type WorkspaceAPI } from '@tavo/shared';
import type { components } from './generated';

type Schemas = components['schemas'];
const profileSchema = z.object({ backendUrl: z.string().url(), authenticated: z.boolean(), startMinimized: z.boolean() });
const runSchema = z.object({ id: z.string(), eventId: z.string(), status: z.enum(['pending', 'running', 'succeeded', 'failed', 'cancelled']), progress: z.number(), errorCode: z.string().nullable(), result: analysisSchema.nullable(), createdAt: z.string(), startedAt: z.string().nullable(), finishedAt: z.string().nullable() });
export type AgentRun = z.infer<typeof runSchema>;
export class APIError extends AppError {}
const sleep = (milliseconds: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  const abort = () => { clearTimeout(timer); reject(new APIError('CANCELLED')); };
  const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, milliseconds);
  signal?.addEventListener('abort', abort, { once: true }); if (signal?.aborted) abort();
});
export function parseSSE(frame: string): BusinessEvent | null {
  const kind = frame.split('\n').find((line) => line.startsWith('event:'))?.slice(6).trim();
  if (kind !== 'business') return null;
  const body = frame.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
  try { return businessEventSchema.parse(JSON.parse(body)); } catch { throw new APIError('INVALID_DATA'); }
}
export class APIClient implements WorkspaceAPI {
  constructor(private readonly native: () => NativeDesktopAPI = () => window.desktop, private readonly transport: typeof fetch = (...args) => fetch(...args)) {}
  async profile() { return profileSchema.parse(await this.native().profile()); }
  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const profile = await this.profile(); const timeout = AbortSignal.timeout(5000);
    try {
      const response = await this.transport(new URL(`/api/v1${path}`, profile.backendUrl), { ...options, credentials: 'omit', redirect: 'error', signal: options.signal ? AbortSignal.any([options.signal, timeout]) : timeout, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
      if (!response.ok) {
        const body = await response.json().catch(() => null); const parsed = errorCodeSchema.safeParse(body?.error?.code);
        throw new APIError(parsed.success ? parsed.data : response.status === 401 ? 'UNAUTHENTICATED' : 'INTERNAL');
      }
      return (response.status === 204 ? undefined : await response.json()) as T;
    } catch (error) {
      if (error instanceof AppError) throw error;
      if (options.signal?.aborted) throw new APIError('CANCELLED');
      throw new APIError(timeout.aborted ? 'TIMEOUT' : 'BACKEND_UNAVAILABLE');
    }
  }
  async getSnapshot(signal?: AbortSignal): Promise<Snapshot> {
    const raw = await this.request<Schemas['Snapshot']>('/snapshot', { signal });
    const profile = await this.profile(); return snapshotSchema.parse({ ...raw, settings: { ...raw.settings, startMinimized: profile.startMinimized } });
  }
  async snapshot(): Promise<Response> { try { return { ok: true, data: await this.getSnapshot() }; } catch (error) { return { ok: false, error: publicError(error) }; } }
  private async mutate(path: string, method: string, body?: unknown): Promise<Response> {
    try { await this.request(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }); return { ok: true, data: await this.getSnapshot() }; }
    catch (error) { return { ok: false, error: publicError(error) }; }
  }
  addConnection: WorkspaceAPI['addConnection'] = (input) => this.mutate('/accounts', 'POST', input);
  disconnect: WorkspaceAPI['disconnect'] = ({ connectionId }) => this.mutate(`/accounts/${encodeURIComponent(connectionId)}/disconnect`, 'POST');
  removeConnection: WorkspaceAPI['removeConnection'] = ({ connectionId }) => this.mutate(`/accounts/${encodeURIComponent(connectionId)}`, 'DELETE');
  reconnect: WorkspaceAPI['reconnect'] = ({ connectionId }) => this.mutate(`/accounts/${encodeURIComponent(connectionId)}/reconnect`, 'POST');
  syncConnection: WorkspaceAPI['syncConnection'] = ({ connectionId }) => this.mutate(`/accounts/${encodeURIComponent(connectionId)}/sync`, 'POST');
  cancelSync: WorkspaceAPI['cancelSync'] = ({ connectionId }) => this.mutate(`/accounts/${encodeURIComponent(connectionId)}/sync/cancel`, 'POST');
  configureMock: WorkspaceAPI['configureMock'] = ({ connectionId, config }) => this.mutate(`/accounts/${encodeURIComponent(connectionId)}/mock`, 'PATCH', config);
  updateSettings: WorkspaceAPI['updateSettings'] = async (settings) => {
    const result = await this.mutate('/settings', 'PATCH', { ...settings, startMinimized: false });
    if (!result.ok) return result;
    try { await this.native().preferences({ startMinimized: settings.startMinimized }); return this.snapshot(); }
    catch { return { ok: false, error: publicError(new APIError('INTERNAL')) }; }
  };
  async analyze(input: { eventId: string }): Promise<Response> {
    try {
      const queued = await this.request<Schemas['RunOut']>('/agent/analyze', { method: 'POST', body: JSON.stringify(input) });
      for (let attempt = 0; attempt < 40; attempt++) {
        const run = await this.getRun(queued.id);
        if (run.status === 'succeeded') return this.snapshot();
        if (run.status === 'failed' || run.status === 'cancelled') throw new APIError('AGENT_FAILED');
        await sleep(250);
      }
      throw new APIError('TIMEOUT');
    } catch (error) { return { ok: false, error: publicError(error) }; }
  }
  async getRun(id: string) { return runSchema.parse(await this.request<Schemas['RunOut']>(`/agent/runs/${encodeURIComponent(id)}`)); }
  async runs() { const page = await this.request<Schemas['RunPage']>('/agent/runs'); return { ...page, items: page.items.map((run) => runSchema.parse(run)) }; }
  async systemStatus() { return this.request<Schemas['SystemStatus']>('/system/status'); }
  async events(options: { before?: number; correlationId?: string; eventType?: string } = {}) {
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(options)) if (value !== undefined && value !== '') query.set(name, String(value));
    const page = await this.request<Schemas['EventPage']>(`/events?${query}`);
    return { ...page, items: page.items.map((event) => businessEventSchema.parse(event)) };
  }
  async subscribe(after: number, signal: AbortSignal, onEvent: (event: BusinessEvent) => void, onStatus: (connected: boolean) => void) {
    let sequence = after; let delay = 500;
    while (!signal.aborted) {
      try {
        const profile = await this.profile();
        const response = await this.transport(`${profile.backendUrl}/api/v1/events/stream?after=${sequence}`, { headers: { 'Last-Event-ID': String(sequence) }, signal, credentials: 'omit', redirect: 'error' });
        if (!response.ok || !response.body) throw new APIError('BACKEND_UNAVAILABLE');
        onStatus(true); delay = 500;
        const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
        try {
          while (!signal.aborted) {
            const chunk = await reader.read(); if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, '\n');
            if (buffer.length > 1_000_000) throw new APIError('INVALID_DATA');
            let end;
            while ((end = buffer.indexOf('\n\n')) >= 0) {
              const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
              const event = parseSSE(frame); if (event && event.sequence > sequence) { sequence = event.sequence; onEvent(event); }
            }
          }
        } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      } catch { /* Reconnect from the last validated persistent sequence. */ }
      onStatus(false); if (signal.aborted) break;
      try { await sleep(delay, signal); } catch { break; } delay = Math.min(10_000, delay * 2);
    }
  }
}
export const api = new APIClient();
