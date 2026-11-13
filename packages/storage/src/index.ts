import { createHash } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import Database from 'better-sqlite3';
import { and, desc, eq } from 'drizzle-orm';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { AppError, accountEventSchema, analysisSchema, connectorSchema, snapshotSchema, syncResultSchema, taskSchema, type AccountEvent, type Analysis, type ConnectorInfo, type ErrorCode, type Snapshot, type Storage, type SyncResult, type SyncTask } from '@tavo/shared';
import * as schema from './schema';
import { migrations } from './migrations';
import { SqliteWorkspaceRepository, workspaceSchema } from './workspace';

export class SqliteStorage implements Storage {
  private readonly client: Database.Database;
  private readonly db;
  readonly workspace: SqliteWorkspaceRepository;
  constructor(path: string) {
    try {
      if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
      this.client = new Database(path);
      try {
        this.client.pragma('foreign_keys = ON');
        this.client.pragma('busy_timeout = 5000');
        this.client.pragma('journal_mode = WAL');
        this.migrate();
        this.db = drizzle(this.client, { schema });
        this.workspace = new SqliteWorkspaceRepository(drizzle(this.client, { schema: workspaceSchema }));
      } catch (error) { this.client.close(); throw error; }
    } catch { throw new AppError('STORAGE_FAILED'); }
  }
  private guard<T>(operation: () => T): T {
    try { return operation(); }
    catch (error) { if (error instanceof AppError) throw error; throw new AppError('STORAGE_FAILED'); }
  }
  private migrate(): void {
    this.client.transaction(() => {
      this.client.exec('CREATE TABLE IF NOT EXISTS _migrations (version INTEGER PRIMARY KEY, hash TEXT NOT NULL)');
      const applied = this.client.prepare('SELECT version, hash FROM _migrations ORDER BY version').all() as { version: number; hash: string }[];
      if (applied.some((row) => !migrations.some((migration) => migration.version === row.version))) throw new AppError('STORAGE_FAILED');
      for (const migration of migrations) {
        const hash = createHash('sha256').update(migration.sql).digest('hex');
        const previous = applied.find((row) => row.version === migration.version);
        if (previous && previous.hash !== hash) throw new AppError('STORAGE_FAILED');
        if (!previous) {
          this.client.exec(migration.sql);
          this.client.prepare('INSERT INTO _migrations (version, hash) VALUES (?, ?)').run(migration.version, hash);
        }
      }
    }).exclusive();
  }
  registerConnector(info: ConnectorInfo): void {
    this.guard(() => {
      const valid = connectorSchema.parse(info);
      const values = { ...valid, permissions: valid.permissions as ['read:mock'] };
      this.db.insert(schema.connectors).values(values).onConflictDoUpdate({
        target: schema.connectors.id,
        set: { name: values.name, capabilities: values.capabilities, permissions: values.permissions },
      }).run();
    });
  }
  snapshot(): Snapshot {
    return this.guard(() => snapshotSchema.parse({
      connectors: this.db.select().from(schema.connectors).all(),
      accounts: this.db.select().from(schema.accounts).orderBy(schema.accounts.id).limit(100).all(),
      events: this.db.select().from(schema.events).orderBy(desc(schema.events.occurredAt), schema.events.id).limit(100).all(),
      tasks: this.db.select().from(schema.syncTasks).orderBy(desc(schema.syncTasks.startedAt), desc(schema.syncTasks.id)).limit(50).all(),
      ...this.workspace.snapshot(),
    }));
  }
  startTask(task: SyncTask): void {
    this.guard(() => this.db.insert(schema.syncTasks).values(taskSchema.parse(task)).run());
  }
  commitSync(taskId: string, connectorId: string, input: SyncResult, at: string): void {
    this.guard(() => {
      const result = syncResultSchema.parse(input);
      this.db.transaction((tx) => {
        const task = tx.select().from(schema.syncTasks).where(eq(schema.syncTasks.id, taskId)).get();
        if (!task || task.connectorId !== connectorId || task.status !== 'running') throw new AppError('INVALID_INPUT');
        for (const account of result.accounts) {
          if (account.connectorId !== connectorId) throw new AppError('FORBIDDEN');
          tx.insert(schema.accounts).values(account).onConflictDoUpdate({
            target: [schema.accounts.connectorId, schema.accounts.providerAccountId],
            set: { name: account.name, balance: account.balance, currency: account.currency, updatedAt: account.updatedAt },
          }).run();
        }
        for (const event of result.events) {
          if (event.connectorId !== connectorId || event.analysis !== null) throw new AppError('FORBIDDEN');
          tx.insert(schema.events).values(event).onConflictDoNothing({ target: [schema.events.connectorId, schema.events.providerEventId] }).run();
        }
        tx.update(schema.connectors).set({ lastSyncAt: at }).where(eq(schema.connectors.id, connectorId)).run();
        tx.update(schema.syncTasks).set({ status: 'succeeded', finishedAt: at }).where(eq(schema.syncTasks.id, taskId)).run();
      });
    });
  }
  failTask(taskId: string, code: ErrorCode, at: string): void {
    this.guard(() => this.db.update(schema.syncTasks).set({ status: 'failed', errorCode: code, finishedAt: at }).where(and(eq(schema.syncTasks.id, taskId), eq(schema.syncTasks.status, 'running'))).run());
  }
  getEvent(id: string): AccountEvent | undefined {
    return this.guard(() => {
      const row = this.db.select().from(schema.events).where(eq(schema.events.id, id)).get();
      return row ? accountEventSchema.parse(row) : undefined;
    });
  }
  saveAnalysis(input: Analysis): void {
    this.guard(() => {
      const analysis = analysisSchema.parse(input);
      const result = this.db.update(schema.events).set({ analysis }).where(eq(schema.events.id, analysis.eventId)).run();
      if (result.changes !== 1) throw new AppError('NOT_FOUND');
    });
  }
  deleteAccount(id: string): void {
    this.guard(() => this.db.delete(schema.accounts).where(eq(schema.accounts.id, id)).run());
  }
  recoverInterruptedTasks(at: string): void {
    this.guard(() => this.db.update(schema.syncTasks).set({ status: 'interrupted', finishedAt: at, errorCode: 'INTERRUPTED' }).where(eq(schema.syncTasks.status, 'running')).run());
  }
  close(): void { this.guard(() => this.client.close()); }
}
export { SqliteEventRepository, SqliteWorkspaceRepository } from './workspace';
