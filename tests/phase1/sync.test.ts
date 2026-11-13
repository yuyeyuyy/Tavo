import { afterEach, describe, expect, it } from 'vitest';
import { retryDelay } from '@tavo/core';
import { fixture, type Fixture } from './fixture';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

describe('持久化同步引擎', () => {
  let f: Fixture; const directories: string[] = []; afterEach(async () => { await f?.close(); for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true }); });
  it('正常同步与增量空页：同一批数据不重复产生变化', async () => {
    f = await fixture(); const a = await f.add();
    expect((await f.sync(a.id)).status).toBe('succeeded'); expect(f.storage.workspace.getConnection(a.id)?.cursor).toBe('revision:1');
    await f.sync(a.id); expect(f.runtime.snapshot().data).toHaveLength(1);
    expect(f.runtime.snapshot().businessEvents.filter((e) => e.eventType === 'data.changed')).toHaveLength(1);
    f.storage.workspace.configureMock(a.id, { ...a.mock, usageUnits: 120, resourceStatus: 'degraded' });
    await f.sync(a.id);
    const changes = f.runtime.snapshot().businessEvents.filter((e) => e.eventType === 'data.changed');
    expect(changes).toHaveLength(2); expect(changes[0]?.payload.changedFields).toEqual(['usageUnits', 'resourceStatus']);
  });
  it.each(['network_once', 'rate_limit_once', 'server_once'] as const)('%s 有限重试并保存次数', async (scenario) => {
    f = await fixture(); const a = await f.add(); f.storage.workspace.configureMock(a.id, { ...a.mock, scenario });
    const pending = await f.sync(a.id); expect(pending).toMatchObject({ status: 'pending', attempts: 1 });
    const delay = Date.parse(pending.nextAttemptAt) - f.now().getTime(); expect(delay).toBeGreaterThanOrEqual(scenario === 'rate_limit_once' ? 1500 : 500);
    f.advance(delay); f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle();
    expect(f.storage.workspace.getJob(pending.id)).toMatchObject({ status: 'succeeded', attempts: 2, retryCount: 1 });
  });
  it.each([['auth', 'AUTH_EXPIRED', 'expired'], ['forbidden', 'PERMISSION_DENIED', 'error'], ['invalid', 'INVALID_DATA', 'error']] as const)('%s 不自动重试', async (scenario, errorCode, status) => {
    f = await fixture(); const a = await f.add(); f.storage.workspace.configureMock(a.id, { ...a.mock, scenario });
    expect(await f.sync(a.id)).toMatchObject({ status: 'failed', attempts: 1, errorCode });
    expect(f.storage.workspace.getConnection(a.id)?.status).toBe(status); expect(f.storage.workspace.getConnection(a.id)?.cursor).toBeNull();
  });
  it('超时最多三次，并保留上一次成功数据和游标', async () => {
    f = await fixture({ timeoutMs: 30 }); const a = await f.add(); await f.sync(a.id);
    const before = f.storage.workspace.getData(a.id);
    f.storage.workspace.configureMock(a.id, { ...a.mock, balance: '1.00', scenario: 'timeout' });
    const job = await f.sync(a.id);
    for (let i = 0; i < 2; i++) { f.advance(60_000); f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle(); }
    expect(f.storage.workspace.getJob(job.id)).toMatchObject({ status: 'failed', attempts: 3, retryCount: 2, errorCode: 'TIMEOUT' });
    expect(f.storage.workspace.getData(a.id)).toEqual(before); expect(f.storage.workspace.getConnection(a.id)?.cursor).toBe('revision:1');
  });
  it('同账户互斥，不同账户受全局并发上限约束', async () => {
    f = await fixture({ concurrency: 1 }); const a = await f.add(); const b = await f.add('demo-b');
    const first = f.runtime.syncEngine.enqueue(a.id);
    expect(() => f.runtime.syncEngine.enqueue(a.id)).toThrow('BUSY'); const second = f.runtime.syncEngine.enqueue(b.id);
    f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle();
    expect(f.storage.workspace.getJob(first.id)?.status).toBe('succeeded'); expect(f.storage.workspace.getJob(second.id)?.status).toBe('pending');
    f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle(); expect(f.storage.workspace.getJob(second.id)?.status).toBe('succeeded');
  });
  it('崩溃遗留 running 按剩余次数恢复，保留 correlationId', async () => {
    f = await fixture(); const a = await f.add(); const job = f.runtime.syncEngine.enqueue(a.id);
    f.storage.workspace.startJob(job.id, f.now().toISOString()); f.runtime.syncEngine.recover();
    expect(f.storage.workspace.getJob(job.id)).toMatchObject({ status: 'pending', errorCode: 'INTERRUPTED' });
    f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle(); expect(f.storage.workspace.getJob(job.id)).toMatchObject({ status: 'succeeded', attempts: 2 });
    expect(f.runtime.snapshot().businessEvents.filter((e) => e.eventType.startsWith('sync.')).every((e) => e.correlationId === job.id)).toBe(true);
  });
  it('落库任一环节失败，数据、游标与变化事件一起回滚', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'tavo-rollback-')); directories.push(directory);
    const path = join(directory, 'test.sqlite'); f = await fixture({}, path);
    const a = await f.add(); const job = f.runtime.syncEngine.enqueue(a.id); f.storage.workspace.startJob(job.id, f.now().toISOString());
    const context = { connection: a, cursor: null, attempt: 1, signal: new AbortController().signal, credential: await f.credentials.get(a.credentialRef) };
    const result = await f.connector.syncAccount(context);
    // 合法数据到达后，最终事件写入失败：验证此前写入的数据和游标也必须回滚。
    const raw = new Database(path);
    raw.exec("CREATE TRIGGER fail_completion BEFORE INSERT ON business_events WHEN NEW.event_type='sync.completed' BEGIN SELECT RAISE(ABORT,'synthetic database failure'); END"); raw.close();
    expect(() => f.storage.workspace.commitJob(job.id, result, f.now().toISOString())).toThrow('STORAGE_FAILED');
    expect(f.storage.workspace.getData(a.id)).toBeUndefined(); expect(f.storage.workspace.getConnection(a.id)?.cursor).toBeNull();
    expect(f.storage.workspace.getJob(job.id)?.status).toBe('running'); expect(f.runtime.snapshot().businessEvents.some((e) => e.eventType === 'data.changed')).toBe(false);
  });
  it('定时同步独立于 UI，关闭自动同步后不再新建任务', async () => {
    f = await fixture(); const a = await f.add();
    f.storage.workspace.updateSettings({ autoSync: true, syncIntervalSeconds: 10, startMinimized: false }, f.now().toISOString());
    f.advance(10_000); f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle(); expect(f.runtime.snapshot().jobs[0]).toMatchObject({ trigger: 'scheduled', status: 'succeeded' });
    f.storage.workspace.updateSettings({ autoSync: false, syncIntervalSeconds: 10, startMinimized: false }, f.now().toISOString());
    f.advance(20_000); f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle(); expect(f.runtime.snapshot().jobs).toHaveLength(1); expect(f.storage.workspace.getData(a.id)).toBeDefined();
  });
  it('断开取消正在执行的任务，晚到结果不能重新写回', async () => {
    f = await fixture(); const a = await f.add(); f.storage.workspace.configureMock(a.id, { ...a.mock, scenario: 'timeout' });
    f.runtime.syncEngine.enqueue(a.id); f.runtime.syncEngine.tick(); await new Promise((resolve) => setTimeout(resolve, 1));
    await f.runtime.execute('disconnect', { connectionId: a.id });
    expect(f.runtime.snapshot().jobs[0]?.status).toBe('cancelled'); expect(f.storage.workspace.getData(a.id)).toBeUndefined();
    expect(f.storage.workspace.getConnection(a.id)?.status).toBe('disconnected');
  });
  it('指数退避有抖动、上限，并尊重 Retry-After', () => {
    expect(retryDelay(1, () => 0)).toBe(500); expect(retryDelay(2, () => 1)).toBe(2000);
    expect(retryDelay(100, () => 1)).toBe(30_000); expect(retryDelay(1, () => 0, 20_000)).toBe(20_000);
  });
  it('忽略取消的连接器：晚到结果丢弃，重试不与旧请求重叠', async () => {
    f = await fixture({ timeoutMs: 15, concurrency: 1 }); const a = await f.add();
    const original = f.connector.syncAccount.bind(f.connector);
    let release!: () => void; let calls = 0;
    f.connector.syncAccount = async (context) => {
      calls++; if (calls === 1) await new Promise<void>((resolve) => { release = resolve; });
      return original({ ...context, signal: new AbortController().signal });
    };
    const job = await f.sync(a.id); expect(job.status).toBe('pending');
    f.advance(60_000); f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle(); expect(calls).toBe(1);
    expect(() => f.runtime.syncEngine.enqueue(a.id)).toThrow('BUSY');
    release(); await new Promise((resolve) => setTimeout(resolve, 5));
    expect(f.storage.workspace.getData(a.id)).toBeUndefined(); expect(f.storage.workspace.getConnection(a.id)?.cursor).toBeNull();
    f.runtime.syncEngine.tick(); await f.runtime.syncEngine.settle();
    expect(calls).toBe(2); expect(f.storage.workspace.getJob(job.id)?.status).toBe('succeeded');
  });
  it('耗尽重试次数的崩溃任务明确失败，不继续恢复', async () => {
    f = await fixture({ maxAttempts: 1 }); const a = await f.add(); const job = f.runtime.syncEngine.enqueue(a.id);
    f.storage.workspace.startJob(job.id, f.now().toISOString()); f.runtime.syncEngine.recover();
    expect(f.storage.workspace.getJob(job.id)).toMatchObject({ status: 'failed', errorCode: 'INTERRUPTED', attempts: 1 });
  });
  it('凭据读取超时后，不启动晚到的连接器请求', async () => {
    f = await fixture({ timeoutMs: 15 }); const a = await f.add(); const original = f.credentials.get.bind(f.credentials);
    let release!: () => void; let calls = 0; const fetch = f.connector.syncAccount.bind(f.connector);
    f.connector.syncAccount = async (context) => { calls++; return fetch(context); };
    f.credentials.get = async (reference) => { const value = await original(reference); await new Promise<void>((resolve) => { release = resolve; }); return value; };
    const job = await f.sync(a.id); expect(job.errorCode).toBe('TIMEOUT'); release();
    await new Promise((resolve) => setTimeout(resolve, 5)); expect(calls).toBe(0); expect(f.storage.workspace.getData(a.id)).toBeUndefined();
  });
});
