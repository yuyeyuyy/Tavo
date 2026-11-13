import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { SqliteStorage } from '@tavo/storage';
import { MockConnector } from '@tavo/connectors';
import { MockAgentRuntime } from '@tavo/agent';
import type { SyncResult } from '@tavo/shared';

describe('SQLite / Drizzle', () => {
  let folder: string; let path: string; let store: SqliteStorage; let result: SyncResult;
  const at = '2026-11-12T00:00:00.000Z';
  beforeEach(async () => {
    folder = mkdtempSync(join(tmpdir(), 'tavo-unit-')); path = join(folder, 'tavo.sqlite');
    store = new SqliteStorage(path);
    const connector = new MockConnector(() => new Date(at)); await connector.initialize();
    result = await connector.sync(); store.registerConnector(connector.info);
  });
  afterEach(() => { store.close(); rmSync(folder, { recursive: true, force: true }); });
  function task(id: string) { store.startTask({ id, connectorId: 'mock', status: 'running', startedAt: at, finishedAt: null, errorCode: null }); }
  it('完成 CRUD，重启后保留账户、事件和分析，迁移可重复执行', async () => {
    task('first'); store.commitSync('first', 'mock', result, at);
    expect(store.snapshot().accounts).toHaveLength(2);
    const analysis = await new MockAgentRuntime().analyze(result.events[0]!);
    store.saveAnalysis(analysis);
    const changed = structuredClone(result); changed.accounts[0]!.balance = '7.50';
    task('second'); store.commitSync('second', 'mock', changed, at);
    store.close(); store = new SqliteStorage(path);
    expect(store.snapshot().accounts.find((a) => a.id === result.accounts[0]!.id)?.balance).toBe('7.50');
    expect(store.getEvent(analysis.eventId)?.analysis).toEqual(analysis);
    expect(store.snapshot().events).toHaveLength(2);
    store.deleteAccount(result.accounts[0]!.id);
    expect(store.snapshot().accounts).toHaveLength(1);
    expect(store.getEvent(analysis.eventId)).toBeUndefined();
  });
  it('外键失败回滚整个同步事务', () => {
    const broken = structuredClone(result); broken.events[1]!.accountId = 'missing';
    task('rollback'); expect(() => store.commitSync('rollback', 'mock', broken, at)).toThrow('STORAGE_FAILED');
    expect(store.snapshot().accounts).toEqual([]); expect(store.snapshot().events).toEqual([]);
    expect(store.snapshot().connectors[0]?.lastSyncAt).toBeNull();
    expect(store.snapshot().tasks[0]?.status).toBe('running');
  });
  it('同一上游账户和事件幂等，重注册保留同步时间', () => {
    for (const id of ['one', 'two']) { task(id); store.commitSync(id, 'mock', result, at); }
    store.registerConnector(new MockConnector().info);
    expect(store.snapshot().accounts).toHaveLength(2); expect(store.snapshot().events).toHaveLength(2);
    expect(store.snapshot().tasks).toHaveLength(2); expect(store.snapshot().connectors[0]?.lastSyncAt).toBe(at);
  });
  it('启动恢复时把未完成任务标记为 interrupted', () => {
    task('pending'); store.recoverInterruptedTasks(at);
    expect(store.snapshot().tasks[0]).toMatchObject({ status: 'interrupted', errorCode: 'INTERRUPTED', finishedAt: at });
  });
  it('拒绝被改动的迁移记录与未知新版本', () => {
    const raw = new Database(path);
    raw.prepare('UPDATE _migrations SET hash = ? WHERE version = 1').run('tampered');
    expect(() => new SqliteStorage(path)).toThrow('STORAGE_FAILED');
    raw.prepare('INSERT INTO _migrations VALUES (?, ?)').run(999, 'future');
    expect(() => new SqliteStorage(path)).toThrow('STORAGE_FAILED'); raw.close();
  });
  it('缺失的事件不应伪装成分析保存成功', async () => {
    const analysis = await new MockAgentRuntime().analyze(result.events[0]!);
    expect(() => store.saveAnalysis(analysis)).toThrow('NOT_FOUND');
  });
});
