import { AppError, connectorSchema, syncResultSchema, type Capability, type ConnectorInfo, type SyncResult } from '@tavo/shared';
import { connectionSyncResultSchema, type ConnectionRecord, type ConnectionSyncResult, type ErrorCode } from '@tavo/shared';

export interface SyncContext { connection: ConnectionRecord; credential: string; cursor: string | null; attempt: number; signal: AbortSignal }
export class ConnectorError extends AppError {
  constructor(code: ErrorCode, public readonly retryAfterMs = 0) { super(code); }
}

export interface Connector {
  readonly info: ConnectorInfo;
  initialize(): Promise<void>;
  sync(): Promise<SyncResult>;
  dispose(): Promise<void>;
  syncAccount?(context: SyncContext): Promise<ConnectionSyncResult>;
}

export class ConnectorRegistry {
  private readonly connectors = new Map<string, Connector>();

  async register(connector: Connector): Promise<void> {
    connectorSchema.parse(connector.info);
    if (this.connectors.has(connector.info.id)) throw new AppError('INVALID_INPUT');
    await connector.initialize();
    this.connectors.set(connector.info.id, connector);
  }
  get(id: string, capability?: Capability): Connector {
    const connector = this.connectors.get(id);
    if (!connector) throw new AppError('NOT_FOUND');
    if (capability && !connector.info.capabilities.includes(capability)) throw new AppError('FORBIDDEN');
    return connector;
  }
  list(): ConnectorInfo[] { return [...this.connectors.values()].map(({ info }) => connectorSchema.parse(info)); }
  async unregister(id: string): Promise<void> {
    const connector = this.get(id);
    await connector.dispose();
    this.connectors.delete(id);
  }
  async dispose(): Promise<void> {
    for (const id of [...this.connectors.keys()]) await this.unregister(id);
  }
}

export class MockConnector implements Connector {
  readonly info: ConnectorInfo = {
    id: 'mock', name: 'Mock Connector', capabilities: ['account', 'balance', 'usage', 'resource', 'event'],
    permissions: ['read:mock'], lastSyncAt: null,
  };
  private ready = false;
  constructor(private readonly now: () => Date = () => new Date(), private readonly latencyMs = 150) {}
  async initialize(): Promise<void> { this.ready = true; }
  async dispose(): Promise<void> { this.ready = false; }
  async syncAccount(context: SyncContext): Promise<ConnectionSyncResult> {
    if (!this.ready) throw new ConnectorError('CONNECTOR_FAILED');
    if (!context.credential.startsWith('mock-only:')) throw new ConnectorError('AUTH_EXPIRED');
    const { connection, attempt, signal } = context;
    const scenario = connection.mock.scenario;
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new ConnectorError('CANCELLED')); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, scenario === 'timeout' ? 120_000 : this.latencyMs);
      signal.addEventListener('abort', abort, { once: true }); if (signal.aborted) abort();
    });
    if (scenario === 'auth') throw new ConnectorError('AUTH_EXPIRED');
    if (scenario === 'forbidden') throw new ConnectorError('PERMISSION_DENIED');
    if (attempt === 1 && scenario === 'network_once') throw new ConnectorError('NETWORK');
    if (attempt === 1 && scenario === 'rate_limit_once') throw new ConnectorError('RATE_LIMITED', 1500);
    if (attempt === 1 && scenario === 'server_once') throw new ConnectorError('SERVER_ERROR');
    if (scenario === 'invalid') throw new ConnectorError('INVALID_DATA');
    const nextCursor = `revision:${connection.revision}`;
    return connectionSyncResultSchema.parse({
      nextCursor,
      data: context.cursor === nextCursor ? null : {
        connectionId: connection.id, providerAccountId: connection.externalAccountId,
        balance: connection.mock.balance, currency: connection.mock.currency, usageUnits: connection.mock.usageUnits,
        resourceStatus: connection.mock.resourceStatus, revision: connection.revision,
        updatedAt: this.now().toISOString(), source: 'mock',
      },
    });
  }
  async sync(): Promise<SyncResult> {
    if (!this.ready) throw new AppError('CONNECTOR_FAILED');
    const at = this.now().toISOString();
    const fixtures = [
      { providerAccountId: 'demo-model', name: '模型服务 · 模拟账户', balance: '8.50', currency: 'USD' },
      { providerAccountId: 'demo-cloud', name: '云资源 · 模拟账户', balance: '128.00', currency: 'CNY' },
    ];
    // providerEventId 为固定上游记录 ID；重复拉取同一条记录不会重复新增事件。
    return syncResultSchema.parse({
      accounts: fixtures.map((item) => ({ ...item, id: `mock:${item.providerAccountId}`, connectorId: 'mock', updatedAt: at, source: 'mock' })),
      events: fixtures.map((item) => ({
        id: `mock:initial:${item.providerAccountId}`, connectorId: 'mock', accountId: `mock:${item.providerAccountId}`,
        providerEventId: `initial:${item.providerAccountId}`, kind: 'balance.observed',
        balance: item.balance, currency: item.currency, occurredAt: at, source: 'mock', analysis: null,
      })),
    });
  }
}
