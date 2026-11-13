import { createHash, randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { afterEach, describe, expect, it } from 'vitest';
import { migrations } from '../../packages/storage/src/migrations';
import { fixture, TestEncryptionProvider, type Fixture } from './fixture';

describe('业务事件与升级持久化', () => {
  let f: Fixture | undefined; const directories: string[] = [];
  afterEach(async () => { await f?.close(); f = undefined; for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true }); });
  const path = () => { const directory = mkdtempSync(join(tmpdir(), 'tavo-persist-')); directories.push(directory); return join(directory, 'test.sqlite'); };
  it('事件顺序、去重和进程内发布与持久化一致', async () => {
    f = await fixture(); const a = await f.add(); const job = await f.sync(a.id);
    const events = f.storage.workspace.events.list(a.id).filter((e) => e.correlationId === job.id).reverse();
    expect(events.map((e) => e.eventType)).toEqual(['sync.started', 'data.changed', 'sync.completed']);
    expect(events[0]!.sequence).toBeLessThan(events[1]!.sequence);
    expect(f.published.map((e) => e.eventId)).toEqual(f.storage.workspace.events.list().reverse().map((e) => e.eventId));
    const event = { eventId: randomUUID(), eventType: 'sync.completed' as const, sourceId: 'mock', accountId: a.id, occurredAt: f.now().toISOString(), payload: { attempt: 1 }, correlationId: job.id, dedupeKey: 'stable-source-record' };
    expect(f.storage.workspace.events.append(event)).toBeDefined();
    expect(f.storage.workspace.events.append({ ...event, eventId: randomUUID() })).toBeUndefined();
  });
  it('业务通知观察者出错不改变已提交状态', async () => {
    f = await fixture({ publish: () => { throw new Error('synthetic observer failure'); } });
    const a = await f.add(); expect((await f.sync(a.id)).status).toBe('succeeded');
    expect(f.storage.workspace.getConnection(a.id)?.status).toBe('connected');
  });
  it('变化 A→B→A 分别记录，原样重复无新提醒，分析保留', async () => {
    f = await fixture(); const a = await f.add(); await f.sync(a.id);
    const original = f.runtime.snapshot().businessEvents.find((e) => e.eventType === 'data.changed')!;
    await f.runtime.analyze(original.eventId);
    f.storage.workspace.configureMock(a.id, { ...a.mock, balance: '6.00' }); await f.sync(a.id);
    f.storage.workspace.configureMock(a.id, a.mock); await f.sync(a.id); await f.sync(a.id);
    expect(f.runtime.snapshot().businessEvents.filter((e) => e.eventType === 'data.changed')).toHaveLength(3);
    expect(f.storage.workspace.events.get(original.eventId)?.analysis?.mode).toBe('deterministic-mock');
  });
  it('真实文件重开后恢复 running 任务，游标、设置、密文与事件保留', async () => {
    const file = path(); const provider = new TestEncryptionProvider(); f = await fixture({}, file, provider); const a = await f.add(); await f.sync(a.id);
    f.storage.workspace.updateSettings({ autoSync: true, syncIntervalSeconds: 60, startMinimized: true }, f.now().toISOString());
    const job = f.runtime.syncEngine.enqueue(a.id); f.storage.workspace.startJob(job.id, f.now().toISOString());
    await f.close(); f = undefined;
    f = await fixture({}, file, provider);
    expect(f.storage.workspace.getJob(job.id)).toMatchObject({ status: 'pending', attempts: 1, errorCode: 'INTERRUPTED' });
    expect(f.storage.workspace.getConnection(a.id)?.cursor).toBe('revision:1'); expect(f.runtime.snapshot().settings.startMinimized).toBe(true);
    expect(await f.credentials.get(a.credentialRef)).toMatch(/^mock-only:/);
    f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle(); expect(f.storage.workspace.getJob(job.id)?.status).toBe('succeeded');
    expect(f.runtime.snapshot().businessEvents.filter((e) => e.eventType === 'data.changed')).toHaveLength(1);
  });
  it('从 Phase 0 数据库升级保留旧数据，并建立已断开的连接', async () => {
    const file = path(); const raw = new Database(file); const initial = migrations[0];
    raw.exec('CREATE TABLE _migrations (version INTEGER PRIMARY KEY, hash TEXT NOT NULL)'); raw.exec(initial.sql);
    raw.prepare('INSERT INTO _migrations VALUES (?,?)').run(initial.version, createHash('sha256').update(initial.sql).digest('hex'));
    raw.prepare('INSERT INTO connectors VALUES (?,?,?,?,NULL)').run('mock', 'Mock Connector', '["account","balance","event"]', '["read:mock"]');
    raw.prepare('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)').run('mock:legacy', 'mock', 'legacy', '旧模拟账户', '12.00', 'USD', '2026-11-12T00:00:00.000Z', 'mock'); raw.close();
    f = await fixture({}, file);
    expect(f.runtime.snapshot().accounts[0]?.balance).toBe('12.00');
    expect(f.runtime.snapshot().connections[0]).toMatchObject({ externalAccountId: 'legacy', status: 'disconnected', credentialConfigured: false });
    expect(f.runtime.snapshot().data[0]?.balance).toBe('12.00');
    await f.runtime.execute('reconnect', { connectionId: 'legacy:mock:legacy' }); await f.sync('legacy:mock:legacy');
    expect(f.runtime.snapshot().connections[0]?.status).toBe('connected');
  });
});
