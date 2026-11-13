import { randomUUID } from 'node:crypto';
import { and, asc, desc, eq, isNull, lte, or, sql } from 'drizzle-orm';
import type { BetterSQLite3Database } from 'drizzle-orm/better-sqlite3';
import { AppError, analysisSchema, businessEventSchema, connectionDataSchema, connectionRecordSchema, connectionSchema, connectionSyncResultSchema, idSchema, mockConfigSchema, settingsSchema, syncJobSchema, type Analysis, type BusinessEvent, type ConnectionRecord, type ConnectionSyncResult, type ErrorCode, type EventRepository, type MockConfig, type NewBusinessEvent, type Settings, type SyncJob, type WorkspaceStorage } from '@tavo/shared';
import * as legacy from './schema';
import * as tables from './workspace-schema';

export const workspaceSchema = { ...legacy, ...tables };
type Db = BetterSQLite3Database<typeof workspaceSchema>;
type Executor = Pick<Db, 'select' | 'insert' | 'update' | 'delete'>;
const activeJob = or(eq(tables.syncJobs.status, 'pending'), eq(tables.syncJobs.status, 'running'));
export function storageGuard<T>(operation: () => T): T {
  try { return operation(); } catch (error) { if (error instanceof AppError) throw error; throw new AppError('STORAGE_FAILED'); }
}
function appendOn(db: Executor, event: NewBusinessEvent): BusinessEvent | undefined {
  const { dedupeKey, ...data } = event;
  if (!dedupeKey || dedupeKey.length > 256) throw new AppError('INVALID_INPUT');
  businessEventSchema.parse({ ...data, sequence: 1, analysis: null });
  const row = db.insert(tables.businessEvents).values({ ...event, analysis: null }).onConflictDoNothing().returning().get();
  return row ? businessEventSchema.strip().parse(row) : undefined;
}
function event(connection: ConnectionRecord, type: BusinessEvent['eventType'], correlationId: string, at: string, payload: BusinessEvent['payload'] = {}, dedupeKey = `${correlationId}:${type}`): NewBusinessEvent {
  return { eventId: randomUUID(), eventType: type, accountId: connection.id, sourceId: connection.connectorId, occurredAt: at, payload, correlationId, dedupeKey };
}
export class SqliteEventRepository implements EventRepository {
  constructor(private readonly db: Db) {}
  append(input: NewBusinessEvent) { return storageGuard(() => appendOn(this.db, input)); }
  list(accountId?: string): BusinessEvent[] {
    return storageGuard(() => this.db.select().from(tables.businessEvents).where(accountId ? eq(tables.businessEvents.accountId, accountId) : undefined).orderBy(desc(tables.businessEvents.sequence)).limit(300).all().map((row) => businessEventSchema.strip().parse(row)));
  }
  get(eventId: string) {
    return storageGuard(() => { const row = this.db.select().from(tables.businessEvents).where(eq(tables.businessEvents.eventId, eventId)).get(); return row ? businessEventSchema.strip().parse(row) : undefined; });
  }
}
export class SqliteWorkspaceRepository implements WorkspaceStorage {
  readonly events: EventRepository;
  readonly ciphertexts;
  constructor(private readonly db: Db) {
    this.events = new SqliteEventRepository(db);
    this.ciphertexts = {
      read: (reference: string) => storageGuard(() => this.db.select().from(tables.credentialRecords).where(eq(tables.credentialRecords.reference, idSchema.parse(reference))).get()?.ciphertext),
      write: (reference: string, ciphertext: Uint8Array) => storageGuard(() => {
        idSchema.parse(reference);
        if (!ciphertext.byteLength || ciphertext.byteLength > 1_000_000) throw new AppError('INVALID_INPUT');
        this.db.insert(tables.credentialRecords).values({ reference, ciphertext: Buffer.from(ciphertext) }).onConflictDoUpdate({ target: tables.credentialRecords.reference, set: { ciphertext: Buffer.from(ciphertext) } }).run();
      }),
      delete: (reference: string) => storageGuard(() => { this.db.delete(tables.credentialRecords).where(eq(tables.credentialRecords.reference, idSchema.parse(reference))).run(); }),
      replace: (reference: string, previous: Uint8Array, ciphertext: Uint8Array) => storageGuard(() => {
        idSchema.parse(reference); if (!ciphertext.byteLength || ciphertext.byteLength > 1_000_000) throw new AppError('INVALID_INPUT');
        return this.db.update(tables.credentialRecords).set({ ciphertext: Buffer.from(ciphertext) }).where(and(eq(tables.credentialRecords.reference, reference), eq(tables.credentialRecords.ciphertext, Buffer.from(previous)))).run().changes === 1;
      }),
    };
  }
  getConnection(id: string): ConnectionRecord | undefined {
    return storageGuard(() => { const row = this.db.select().from(tables.connections).where(eq(tables.connections.id, id)).get(); return row ? connectionRecordSchema.parse(row) : undefined; });
  }
  private require(id: string): ConnectionRecord {
    const connection = this.getConnection(id);
    if (!connection || connection.deletedAt) throw new AppError('NOT_FOUND');
    return connection;
  }
  listConnections(includeDeleted = false) {
    return storageGuard(() => this.db.select().from(tables.connections).where(includeDeleted ? undefined : isNull(tables.connections.deletedAt)).orderBy(asc(tables.connections.createdAt), asc(tables.connections.id)).all().map((row) => connectionRecordSchema.parse(row)));
  }
  snapshot() {
    return storageGuard(() => ({
      connections: this.listConnections().map((row) => connectionSchema.strip().parse(row)),
      data: this.db.select().from(tables.connectionData).all().map((row) => connectionDataSchema.parse(row)),
      jobs: this.db.select().from(tables.syncJobs).orderBy(desc(tables.syncJobs.createdAt), desc(sql`${tables.syncJobs}.rowid`)).limit(200).all().map((row) => syncJobSchema.parse(row)),
      businessEvents: this.events.list(), settings: this.getSettings(),
    }));
  }
  addConnection(input: ConnectionRecord): void {
    storageGuard(() => {
      const connection = connectionRecordSchema.parse(input);
      if (this.listConnections().length >= 200) throw new AppError('INVALID_INPUT');
      if (this.listConnections().some((row) => row.connectorId === connection.connectorId && row.externalAccountId === connection.externalAccountId)) throw new AppError('DUPLICATE_CONNECTION');
      this.db.insert(tables.connections).values(connection).run();
    });
  }
  setConnecting(id: string): void {
    storageGuard(() => { this.require(id); this.db.update(tables.connections).set({ status: 'connecting', errorCode: null, credentialConfigured: false }).where(eq(tables.connections.id, id)).run(); });
  }
  finishConnecting(id: string, at: string): BusinessEvent[] {
    return storageGuard(() => this.db.transaction((tx) => {
      const connection = this.require(id);
      if (connection.status !== 'connecting') throw new AppError('INVALID_STATE');
      tx.update(tables.connections).set({ status: 'connected', credentialConfigured: true, cleanupPending: false, errorCode: null, nextSyncAt: this.nextSync(at) }).where(eq(tables.connections.id, id)).run();
      const saved = appendOn(tx, event(connection, 'account.connected', randomUUID(), at)); return saved ? [saved] : [];
    }));
  }
  connectionError(id: string, code: ErrorCode): void {
    storageGuard(() => { this.require(id); this.db.update(tables.connections).set({ status: code === 'AUTH_EXPIRED' ? 'expired' : 'error', errorCode: code }).where(eq(tables.connections.id, id)).run(); });
  }
  beginDisconnect(id: string, remove: boolean, at: string): BusinessEvent[] {
    return storageGuard(() => this.db.transaction((tx) => {
      const connection = this.require(id); const emitted = this.cancelJobs(id, at);
      tx.update(tables.connections).set({ status: 'disconnected', credentialConfigured: false, nextSyncAt: null, cleanupPending: true, deleteRequested: remove, errorCode: null }).where(eq(tables.connections.id, id)).run();
      if (connection.status !== 'disconnected') { const saved = appendOn(tx, event(connection, 'account.disconnected', randomUUID(), at)); if (saved) emitted.push(saved); }
      return emitted;
    }));
  }
  finishCleanup(id: string, at: string): void {
    storageGuard(() => this.db.transaction((tx) => {
      const connection = this.require(id);
      tx.update(tables.connections).set({ cleanupPending: false, credentialConfigured: false, deletedAt: connection.deleteRequested ? at : null }).where(eq(tables.connections.id, id)).run();
      if (connection.deleteRequested) tx.delete(tables.connectionData).where(eq(tables.connectionData.connectionId, id)).run();
    }));
  }
  configureMock(id: string, input: MockConfig): void {
    storageGuard(() => {
      const connection = this.require(id); const config = mockConfigSchema.parse(input);
      if (connection.connectorId !== 'mock') throw new AppError('FORBIDDEN');
      const changed = ['balance', 'currency', 'usageUnits', 'resourceStatus'].some((key) => connection.mock[key as keyof MockConfig] !== config[key as keyof MockConfig]);
      this.db.update(tables.connections).set({ mock: config, revision: connection.revision + (changed ? 1 : 0) }).where(eq(tables.connections.id, id)).run();
    });
  }
  getSettings(): Settings { return storageGuard(() => settingsSchema.parse(this.db.select().from(tables.appSettings).where(eq(tables.appSettings.id, 1)).get()?.value)); }
  private nextSync(at: string): string { return new Date(Date.parse(at) + this.getSettings().syncIntervalSeconds * 1000).toISOString(); }
  updateSettings(input: Settings, at: string): void {
    storageGuard(() => this.db.transaction((tx) => {
      const settings = settingsSchema.parse(input); tx.update(tables.appSettings).set({ value: settings }).where(eq(tables.appSettings.id, 1)).run();
      tx.update(tables.connections).set({ nextSyncAt: this.nextSync(at) }).where(or(eq(tables.connections.status, 'connected'), eq(tables.connections.status, 'error'))).run();
    }));
  }
  getData(id: string) { return storageGuard(() => { const row = this.db.select().from(tables.connectionData).where(eq(tables.connectionData.connectionId, id)).get(); return row ? connectionDataSchema.parse(row) : undefined; }); }
  enqueue(input: SyncJob): void {
    storageGuard(() => {
      const job = syncJobSchema.parse(input); const connection = this.require(job.connectionId);
      if (!connection.credentialConfigured || !['connected', 'error'].includes(connection.status) || connection.cleanupPending) throw new AppError('INVALID_STATE');
      if (this.db.select().from(tables.syncJobs).where(and(eq(tables.syncJobs.connectionId, job.connectionId), activeJob)).get()) throw new AppError('BUSY');
      this.db.insert(tables.syncJobs).values(job).run();
    });
  }
  getJob(id: string) { return storageGuard(() => { const row = this.db.select().from(tables.syncJobs).where(eq(tables.syncJobs.id, id)).get(); return row ? syncJobSchema.parse(row) : undefined; }); }
  dueJobs(at: string): SyncJob[] {
    return storageGuard(() => this.db.select().from(tables.syncJobs).where(and(eq(tables.syncJobs.status, 'pending'), lte(tables.syncJobs.nextAttemptAt, at))).orderBy(asc(tables.syncJobs.createdAt), asc(sql`${tables.syncJobs}.rowid`)).all().map((row) => syncJobSchema.parse(row)));
  }
  startJob(id: string, at: string): BusinessEvent[] {
    return storageGuard(() => this.db.transaction((tx) => {
      const job = this.getJob(id); if (!job || job.status !== 'pending' || job.nextAttemptAt > at) throw new AppError('INVALID_STATE');
      const connection = this.require(job.connectionId);
      if (!['connected', 'error'].includes(connection.status) || !connection.credentialConfigured || connection.cleanupPending) throw new AppError('INVALID_STATE');
      const attempt = job.attempts + 1;
      tx.update(tables.syncJobs).set({ status: 'running', attempts: attempt, retryCount: Math.max(0, attempt - 1), startedAt: job.startedAt ?? at, errorCode: null }).where(eq(tables.syncJobs.id, id)).run();
      const saved = appendOn(tx, event(connection, 'sync.started', id, at, { attempt }, `${id}:start:${attempt}`)); return saved ? [saved] : [];
    }));
  }
  commitJob(id: string, input: ConnectionSyncResult, at: string): BusinessEvent[] {
    return storageGuard(() => this.db.transaction((tx) => {
      const result = connectionSyncResultSchema.parse(input); const job = this.getJob(id);
      if (!job || job.status !== 'running') throw new AppError('CANCELLED');
      const connection = this.require(job.connectionId);
      if (!connection.credentialConfigured || connection.cleanupPending || !['connected', 'error'].includes(connection.status)) throw new AppError('CANCELLED');
      const emitted: BusinessEvent[] = [];
      if (result.data) {
        const data = result.data;
        if (data.connectionId !== connection.id || data.providerAccountId !== connection.externalAccountId) throw new AppError('INVALID_DATA');
        const previous = this.getData(connection.id);
        const changedFields = (['balance', 'currency', 'usageUnits', 'resourceStatus'] as const).filter((key) => !previous || previous[key] !== data[key]);
        if (data.revision !== connection.revision || (previous && data.revision < previous.revision) || (previous && data.revision === previous.revision && changedFields.length > 0)) throw new AppError('INVALID_DATA');
        tx.insert(tables.connectionData).values(data).onConflictDoUpdate({ target: tables.connectionData.connectionId, set: data }).run();
        if (changedFields.length) {
          const saved = appendOn(tx, event(connection, 'data.changed', id, at, { previous: previous ?? null, current: data, changedFields }, `revision:${data.revision}`)); if (saved) emitted.push(saved);
        }
      }
      tx.update(tables.connections).set({ cursor: result.nextCursor, lastSyncAt: at, nextSyncAt: this.nextSync(at), status: 'connected', errorCode: null }).where(eq(tables.connections.id, connection.id)).run();
      tx.update(tables.syncJobs).set({ status: 'succeeded', finishedAt: at, errorCode: null }).where(eq(tables.syncJobs.id, id)).run();
      const saved = appendOn(tx, event(connection, 'sync.completed', id, at, { attempt: job.attempts })); if (saved) emitted.push(saved);
      return emitted;
    }));
  }
  failJob(id: string, code: ErrorCode, nextAttemptAt: string | null, at: string): BusinessEvent[] {
    return storageGuard(() => this.db.transaction((tx) => {
      const job = this.getJob(id); if (!job || job.status === 'cancelled' || job.status === 'succeeded') return [];
      const connection = this.require(job.connectionId);
      tx.update(tables.syncJobs).set({ status: nextAttemptAt ? 'pending' : 'failed', finishedAt: nextAttemptAt ? null : at, errorCode: code, ...(nextAttemptAt ? { nextAttemptAt } : {}) }).where(eq(tables.syncJobs.id, id)).run();
      tx.update(tables.connections).set({ status: code === 'AUTH_EXPIRED' ? 'expired' : 'error', errorCode: code, nextSyncAt: this.nextSync(at) }).where(eq(tables.connections.id, connection.id)).run();
      const type = nextAttemptAt ? 'sync.retry_scheduled' : 'sync.failed';
      const saved = appendOn(tx, event(connection, type, id, at, { errorCode: code, attempt: job.attempts, ...(nextAttemptAt ? { nextAttemptAt } : {}) }, `${id}:${type}:${job.attempts}`)); return saved ? [saved] : [];
    }));
  }
  cancelJobs(connectionId: string, at: string): BusinessEvent[] {
    return storageGuard(() => this.db.transaction((tx) => {
      const connection = this.require(connectionId); const emitted: BusinessEvent[] = [];
      for (const job of tx.select().from(tables.syncJobs).where(and(eq(tables.syncJobs.connectionId, connectionId), activeJob)).all()) {
        tx.update(tables.syncJobs).set({ status: 'cancelled', finishedAt: at, errorCode: 'CANCELLED' }).where(eq(tables.syncJobs.id, job.id)).run();
        const saved = appendOn(tx, event(connection, 'sync.cancelled', job.id, at, { errorCode: 'CANCELLED' })); if (saved) emitted.push(saved);
      }
      return emitted;
    }));
  }
  recoverJobs(at: string): BusinessEvent[] {
    return storageGuard(() => this.db.transaction((tx) => {
      const emitted: BusinessEvent[] = [];
      for (const job of tx.select().from(tables.syncJobs).where(activeJob).all()) {
        const connection = this.require(job.connectionId);
        if (!connection.credentialConfigured || connection.cleanupPending || !['connected', 'error'].includes(connection.status)) emitted.push(...this.cancelJobs(connection.id, at));
        else if (job.status === 'running') emitted.push(...this.failJob(job.id, 'INTERRUPTED', job.attempts < job.maxAttempts ? at : null, at));
      }
      return emitted;
    }));
  }
  saveEventAnalysis(eventId: string, input: Analysis): void {
    storageGuard(() => { const analysis = analysisSchema.parse(input); if (analysis.eventId !== eventId) throw new AppError('INVALID_INPUT');
      if (this.db.update(tables.businessEvents).set({ analysis }).where(and(eq(tables.businessEvents.eventId, eventId), eq(tables.businessEvents.eventType, 'data.changed'))).run().changes !== 1) throw new AppError('NOT_FOUND');
    });
  }
}
