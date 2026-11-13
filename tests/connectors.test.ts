import { describe, expect, it } from 'vitest';
import { ConnectorRegistry, MockConnector } from '@tavo/connectors';
import { syncResultSchema } from '@tavo/shared';

describe('连接器契约与生命周期', () => {
  it('初始化后注册、查询能力、拒绝重复注册并清理', async () => {
    const registry = new ConnectorRegistry();
    const connector = new MockConnector();
    await registry.register(connector);
    expect(registry.list()).toHaveLength(1);
    expect(registry.get('mock', 'balance')).toBe(connector);
    expect(() => registry.get('mock', 'transaction')).toThrow('FORBIDDEN');
    await expect(registry.register(new MockConnector())).rejects.toThrow('INVALID_INPUT');
    await registry.unregister('mock');
    expect(registry.list()).toEqual([]);
    await expect(connector.sync()).rejects.toThrow('CONNECTOR_FAILED');
    expect(() => registry.get('missing')).toThrow('NOT_FOUND');
  });
  it('输出可校验的模拟数据，金额为十进制字符串，上游事件 ID 可重放', async () => {
    const now = new Date('2026-11-12T00:00:00.000Z');
    const connector = new MockConnector(() => now);
    await expect(connector.sync()).rejects.toThrow('CONNECTOR_FAILED');
    await connector.initialize();
    const first = syncResultSchema.parse(await connector.sync());
    expect(first.accounts).toHaveLength(2);
    expect(first.accounts[0]).toMatchObject({ balance: '8.50', source: 'mock', updatedAt: now.toISOString() });
    expect((await connector.sync()).events.map((e) => e.providerEventId)).toEqual(first.events.map((e) => e.providerEventId));
  });
  it('初始化失败不会留下已注册的连接器', async () => {
    const connector = new MockConnector();
    connector.initialize = async () => { throw new Error('synthetic failure'); };
    const registry = new ConnectorRegistry();
    await expect(registry.register(connector)).rejects.toThrow('synthetic failure');
    expect(registry.list()).toEqual([]);
  });
});
