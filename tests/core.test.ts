import { afterEach, describe, expect, it } from 'vitest';
import { ApplicationRuntime, EventBus, SingleFlightTaskManager } from '@tavo/core';
import { MockConnector } from '@tavo/connectors';
import { MockAgentRuntime } from '@tavo/agent';
import { SqliteStorage } from '@tavo/storage';
import { publicError, UnavailableCredentialStore } from '@tavo/shared';

describe('类型安全 Event Bus', () => {
  it('分发到正确的订阅者并可退订', () => {
    const bus = new EventBus<{ done: { count: number }; other: string }>();
    const received: number[] = [];
    const unsubscribe = bus.on('done', ({ count }) => received.push(count));
    bus.emit('other', 'ignored'); bus.emit('done', { count: 2 }); unsubscribe(); bus.emit('done', { count: 4 });
    expect(received).toEqual([2]);
  });
  it('观察者失败不阻断其他监听器', () => {
    let errors = 0; let delivered = false;
    const bus = new EventBus<{ done: void }>(() => errors++);
    bus.on('done', () => { throw new Error('listener'); });
    bus.on('done', () => { delivered = true; }); bus.emit('done', undefined);
    expect(errors).toBe(1); expect(delivered).toBe(true);
  });
});

describe('最小 Task Manager', () => {
  it('拒绝同键并发，并在异常后释放锁', async () => {
    const manager = new SingleFlightTaskManager();
    let release!: () => void;
    const running = manager.run('sync', () => new Promise<void>((resolve) => { release = resolve; }));
    await expect(manager.run('sync', async () => 1)).rejects.toThrow('BUSY');
    release(); await running;
    await expect(manager.run('sync', async () => { throw new Error('failure'); })).rejects.toThrow('failure');
    expect(await manager.run('sync', async () => 3)).toBe(3);
  });
});

describe('Core 端到端业务链路', () => {
  let runtime: ApplicationRuntime | undefined;
  afterEach(async () => { await runtime?.close(); runtime = undefined; });
  it('Mock → SQLite → LangGraph → 持久化结构化分析', async () => {
    runtime = await ApplicationRuntime.create(new SqliteStorage(':memory:'), new MockAgentRuntime(), new MockConnector());
    expect(runtime.snapshot().accounts).toEqual([]);
    const snapshot = await runtime.sync('mock');
    expect(snapshot.tasks[0]?.status).toBe('succeeded');
    const event = snapshot.events.find((e) => e.currency === 'USD')!;
    const analyzed = await runtime.analyze(event.id);
    expect(analyzed.events.find((e) => e.id === event.id)?.analysis).toMatchObject({ severity: 'warning', mode: 'deterministic-mock' });
    await runtime.sync('mock');
    expect(runtime.snapshot().events).toHaveLength(2);
    expect(runtime.snapshot().events.find((e) => e.id === event.id)?.analysis).not.toBeNull();
  });
  it('外部失败保留旧余额，写入失败状态且不泄露异常原文', async () => {
    const connector = new MockConnector();
    runtime = await ApplicationRuntime.create(new SqliteStorage(':memory:'), new MockAgentRuntime(), connector);
    await runtime.sync('mock');
    const before = runtime.snapshot().accounts;
    connector.sync = async () => { throw new Error('synthetic-secret-marker'); };
    await expect(runtime.sync('mock')).rejects.toThrow('CONNECTOR_FAILED');
    expect(runtime.snapshot().accounts).toEqual(before);
    expect(runtime.snapshot().tasks.some((t) => t.status === 'failed' && t.errorCode === 'CONNECTOR_FAILED')).toBe(true);
    expect(JSON.stringify(runtime.snapshot())).not.toContain('synthetic-secret-marker');
    expect(publicError(new Error('synthetic-secret-marker')).message).not.toContain('synthetic-secret-marker');
  });
  it('拒绝其他连接器的账户数据', async () => {
    const connector = new MockConnector();
    runtime = await ApplicationRuntime.create(new SqliteStorage(':memory:'), new MockAgentRuntime(), connector);
    const original = connector.sync.bind(connector);
    connector.sync = async () => { const value = await original(); value.accounts[0]!.connectorId = 'untrusted'; return value; };
    await expect(runtime.sync('mock')).rejects.toThrow('FORBIDDEN');
    expect(runtime.snapshot().accounts).toEqual([]);
  });
  it('未知事件拒绝分析', async () => {
    runtime = await ApplicationRuntime.create(new SqliteStorage(':memory:'), new MockAgentRuntime(), new MockConnector());
    await expect(runtime.analyze('missing')).rejects.toThrow('NOT_FOUND');
  });
});

it('CredentialStore 在不可用时拒绝所有操作', async () => {
  const store = new UnavailableCredentialStore();
  await expect(store.set('mock', 'synthetic-only')).rejects.toThrow('CREDENTIAL_UNAVAILABLE');
  await expect(store.get('mock')).rejects.toThrow('CREDENTIAL_UNAVAILABLE');
  await expect(store.delete('mock')).rejects.toThrow('CREDENTIAL_UNAVAILABLE');
});
