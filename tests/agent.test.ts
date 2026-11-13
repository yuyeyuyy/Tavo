import { MemorySaver } from '@langchain/langgraph';
import { describe, expect, it } from 'vitest';
import { createAnalysisGraph, MockAgentRuntime } from '@tavo/agent';
import { MockConnector } from '@tavo/connectors';

describe('LangGraph 最小状态图', () => {
  it.each([
    ['USD', '8.50', 'warning'], ['USD', '10.00', 'info'],
    ['CNY', '49.99', 'warning'], ['CNY', '50.00', 'info'],
  ] as const)('%s %s 输出 %s', async (currency, balance, severity) => {
    const connector = new MockConnector(); await connector.initialize();
    const event = { ...(await connector.sync()).events[0]!, currency, balance };
    const result = await new MockAgentRuntime().analyze(event);
    expect(result).toMatchObject({ severity, eventId: event.id, mode: 'deterministic-mock' });
    expect(result.evidence).toContain(`模拟余额：${balance} ${currency}`);
  });
  it('注入 MemorySaver 后可以读取 checkpoint（仅内存演示）', async () => {
    const graph = createAnalysisGraph(new MemorySaver());
    const connector = new MockConnector(); await connector.initialize();
    const event = (await connector.sync()).events[0]!;
    const config = { configurable: { thread_id: 'phase0-test' } };
    await graph.invoke({ event }, config);
    const checkpoint = await graph.getState(config);
    expect(checkpoint.values.analysis.eventId).toBe(event.id);
    expect(checkpoint.next).toEqual([]);
  });
});
