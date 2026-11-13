import { randomUUID } from 'node:crypto';
import { ConnectorError, type ConnectorRegistry } from '@tavo/connectors';
import { AppError, connectionSyncResultSchema, publicError, type BusinessEvent, type CredentialAccess, type ErrorCode, type SyncJob, type WorkspaceStorage } from '@tavo/shared';

export const retryableCodes = new Set<ErrorCode>(['NETWORK', 'TIMEOUT', 'RATE_LIMITED', 'SERVER_ERROR', 'INTERRUPTED']);
export function retryDelay(attempt: number, random: () => number = Math.random, retryAfterMs = 0): number {
  const backoff = Math.min(30_000, 1000 * 2 ** Math.max(0, attempt - 1));
  return Math.max(Math.round(backoff * (0.5 + Math.max(0, Math.min(1, random())) * 0.5)), Math.min(86_400_000, Math.max(0, retryAfterMs)));
}
export interface SyncEngineOptions {
  now?: () => Date; random?: () => number; concurrency?: number; timeoutMs?: number; maxAttempts?: number;
  publish?: (events: BusinessEvent[]) => void;
}
export class SyncEngine {
  private readonly active = new Map<string, { controller: AbortController; promise: Promise<void> }>();
  // 即使连接器忽略 AbortSignal，晚到请求仍占用账户与全局名额，避免重叠重试。
  private readonly requests = new Map<string, Promise<unknown>>();
  private readonly now: () => Date;
  private readonly concurrency: number;
  private timer?: ReturnType<typeof setInterval>;
  private closed = false;
  private readonly backgroundFailures: ErrorCode[] = [];
  constructor(private readonly repository: WorkspaceStorage, private readonly registry: ConnectorRegistry, private readonly credentials: CredentialAccess, private readonly options: SyncEngineOptions = {}) {
    this.now = options.now ?? (() => new Date()); this.concurrency = options.concurrency ?? 2;
    if (!Number.isInteger(this.concurrency) || this.concurrency < 1 || this.concurrency > 8 || (options.timeoutMs ?? 5000) < 1 || (options.maxAttempts ?? 3) < 1 || (options.maxAttempts ?? 3) > 5) throw new AppError('INVALID_INPUT');
  }
  private publish(events: BusinessEvent[]) { try { this.options.publish?.(events); } catch { /* 通知错误不改变已提交业务结果。 */ } }
  private at() { return this.now().toISOString(); }
  enqueue(connectionId: string, trigger: SyncJob['trigger'] = 'manual'): SyncJob {
    if (this.closed) throw new AppError('CANCELLED');
    if (this.active.has(connectionId) || this.requests.has(connectionId)) throw new AppError('BUSY');
    const job: SyncJob = { id: randomUUID(), connectionId, trigger, status: 'pending', attempts: 0, retryCount: 0, maxAttempts: this.options.maxAttempts ?? 3, createdAt: this.at(), startedAt: null, finishedAt: null, nextAttemptAt: this.at(), errorCode: null };
    this.repository.enqueue(job); return job;
  }
  recover() { this.publish(this.repository.recoverJobs(this.at())); }
  start() {
    if (this.timer || this.closed) return;
    this.timer = setInterval(() => { try { this.tick(); } catch (error) { this.backgroundFailures.push(publicError(error).code); } }, 250);
    this.timer.unref(); this.tick();
  }
  tick(): void {
    if (this.closed) return;
    const at = this.at();
    if (this.repository.getSettings().autoSync) {
      for (const connection of this.repository.listConnections()) {
        if (connection.deletedAt || !connection.credentialConfigured || !connection.nextSyncAt || connection.nextSyncAt > at || !['connected', 'error'].includes(connection.status) || (connection.errorCode && !retryableCodes.has(connection.errorCode))) continue;
        try { this.enqueue(connection.id, 'scheduled'); } catch (error) { if (!(error instanceof AppError && error.code === 'BUSY')) throw error; }
      }
    }
    for (const job of this.repository.dueJobs(at)) {
      if (new Set([...this.active.keys(), ...this.requests.keys()]).size >= this.concurrency) break;
      if (this.active.has(job.connectionId) || this.requests.has(job.connectionId)) continue;
      const controller = new AbortController();
      // 微任务开始前登记，避免 tick 重入和同账户并发。
      const promise = Promise.resolve().then(() => this.execute(job.id, controller)).catch((error) => {
        this.backgroundFailures.push(publicError(error).code);
      }).finally(() => { this.active.delete(job.connectionId); });
      this.active.set(job.connectionId, { controller, promise });
    }
  }
  async settle(): Promise<void> {
    await Promise.all([...this.active.values()].map((entry) => entry.promise));
    const code = this.backgroundFailures.shift(); if (code) { this.backgroundFailures.length = 0; throw new AppError(code); }
  }
  private async execute(id: string, controller: AbortController): Promise<void> {
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let timedOut = false;
    const abortError = () => new ConnectorError(timedOut ? 'TIMEOUT' : 'CANCELLED');
    try {
      const initial = this.repository.getJob(id);
      if (!initial || initial.status !== 'pending' || controller.signal.aborted) return;
      this.publish(this.repository.startJob(id, this.at()));
      const job = this.repository.getJob(id)!;
      const connection = this.repository.getConnection(job.connectionId)!;
      const connector = this.registry.get(connection.connectorId, 'account');
      if (!connector.syncAccount) throw new ConnectorError('INVALID_DATA');
      timeout = setTimeout(() => { timedOut = true; controller.abort(); }, this.options.timeoutMs ?? 5000);
      let onAbort: (() => void) | undefined;
      const cancelled = new Promise<never>((_resolve, reject) => {
        onAbort = () => reject(abortError()); controller.signal.addEventListener('abort', onAbort, { once: true });
        if (controller.signal.aborted) onAbort();
      });
      let raw;
      try {
        const request = this.credentials.use(connection, (credential) => {
          if (controller.signal.aborted) throw abortError();
          return connector.syncAccount!({ connection, credential, cursor: connection.cursor, attempt: job.attempts, signal: controller.signal });
        });
        this.requests.set(connection.id, request);
        const release = () => { if (this.requests.get(connection.id) === request) this.requests.delete(connection.id); };
        void request.then(release, release);
        raw = await Promise.race([
          request, cancelled,
        ]);
      } finally { if (onAbort) controller.signal.removeEventListener('abort', onAbort); }
      if (controller.signal.aborted) throw abortError();
      const result = connectionSyncResultSchema.safeParse(raw);
      if (!result.success) throw new ConnectorError('INVALID_DATA');
      this.publish(this.repository.commitJob(id, result.data, this.at()));
    } catch (error) {
      const job = this.repository.getJob(id);
      if (!job || job.status === 'cancelled') return;
      const code = controller.signal.aborted ? abortError().code : error instanceof AppError ? error.code : 'CONNECTOR_FAILED';
      if (code === 'CANCELLED') { this.publish(this.repository.cancelJobs(job.connectionId, this.at())); return; }
      const retryAt = retryableCodes.has(code) && job.attempts < job.maxAttempts
        ? new Date(this.now().getTime() + retryDelay(job.attempts, this.options.random, error instanceof ConnectorError ? error.retryAfterMs : 0)).toISOString() : null;
      this.publish(this.repository.failJob(id, code, retryAt, this.at()));
    } finally { clearTimeout(timeout); }
  }
  async cancel(connectionId: string) {
    this.publish(this.repository.cancelJobs(connectionId, this.at()));
    const active = this.active.get(connectionId); active?.controller.abort(); await active?.promise;
  }
  async close() {
    this.closed = true; clearInterval(this.timer); this.timer = undefined;
    for (const [id, entry] of this.active) { this.publish(this.repository.cancelJobs(id, this.at())); entry.controller.abort(); }
    await this.settle();
  }
}
