import { Annotation, END, START, StateGraph, type BaseCheckpointSaver } from '@langchain/langgraph';
import { accountEventSchema, analysisSchema, type AccountEvent, type AgentRuntime, type Analysis } from '@tavo/shared';

export const AnalysisState = Annotation.Root({
  event: Annotation<AccountEvent>(),
  severity: Annotation<'info' | 'warning'>(),
  analysis: Annotation<Analysis>(),
});

// 无模型、无网络、无工具。未来只在这里注入经过授权的只读工具或 checkpointer。
export function createAnalysisGraph(checkpointer?: BaseCheckpointSaver) {
  return new StateGraph(AnalysisState)
    .addNode('classify', (state) => {
      const event = accountEventSchema.parse(state.event);
      // 以整数最小货币单位比较，避免浮点财务计算；各币种阈值独立。
      const [whole = '0', fraction = '00'] = event.balance.split('.');
      const minorUnits = BigInt(whole) * 100n + BigInt(fraction);
      const threshold = event.currency === 'USD' ? 1000n : 5000n;
      return { severity: minorUnits < threshold ? 'warning' as const : 'info' as const };
    })
    .addNode('explain', (state) => ({ analysis: analysisSchema.parse({
      eventId: state.event.id,
      severity: state.severity,
      summary: state.severity === 'warning' ? '模拟账户余额低于演示阈值。' : '模拟账户余额处于演示阈值之上。',
      evidence: [`来源事件：${state.event.id}`, `模拟余额：${state.event.balance} ${state.event.currency}`, `演示阈值：${state.event.currency === 'USD' ? '10.00 USD' : '50.00 CNY'}`],
      recommendation: '仅用于验证本地工作流；请在接入真实平台后核对数据与提醒规则。',
      mode: 'deterministic-mock',
    }) }))
    .addEdge(START, 'classify').addEdge('classify', 'explain').addEdge('explain', END)
    .compile({ checkpointer });
}

export class MockAgentRuntime implements AgentRuntime {
  private readonly graph = createAnalysisGraph();
  async analyze(input: AccountEvent): Promise<Analysis> {
    const event = accountEventSchema.parse(input);
    const result = await this.graph.invoke({ event }, { recursionLimit: 5 });
    return analysisSchema.parse(result.analysis);
  }
}
