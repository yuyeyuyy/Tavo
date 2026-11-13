import { randomUUID } from 'node:crypto';
import { ConnectorRegistry, type Connector } from '@tavo/connectors';
import { AppError, analysisSchema, accountEventSchema, publicError, snapshotSchema, syncResultSchema, type AccountEvent, type AgentRuntime, type Analysis, type Snapshot, type Storage } from '@tavo/shared';

export interface RuntimeEvents {
  'sync.completed': { connectorId: string; taskId: string };
  'sync.failed': { connectorId: string; taskId: string };
  'analysis.completed': Analysis;
}

// 同步监听器；观察者的异常不会把已提交事务误报为业务失败。
export class EventBus<Events extends object> {
  private readonly listeners = new Map<keyof Events, Set<(payload: never) => void>>();
  constructor(private readonly onListenerError: () => void = () => {}) {}
  on<K extends keyof Events>(name: K, listener: (payload: Events[K]) => void): () => void {
    const handlers = this.listeners.get(name) ?? new Set<(payload: never) => void>();
    handlers.add(listener as (payload: never) => void);
    this.listeners.set(name, handlers);
    return () => { handlers.delete(listener as (payload: never) => void); };
  }
  emit<K extends keyof Events>(name: K, payload: Events[K]): void {
    for (const listener of [...(this.listeners.get(name) ?? [])]) {
      try { listener(payload as never); } catch { this.onListenerError(); }
    }
  }
}

export interface TaskManager { run<T>(key: string, operation: () => Promise<T>): Promise<T> }
export class SingleFlightTaskManager implements TaskManager {
  private readonly running = new Set<string>();
  async run<T>(key: string, operation: () => Promise<T>): Promise<T> {
    if (this.running.has(key)) throw new AppError('BUSY');
    this.running.add(key);
    try { return await operation(); } finally { this.running.delete(key); }
  }
}

function validateSyncOwnership(result: ReturnType<typeof syncResultSchema.parse>, connectorId: string): void {
  const accounts = new Map(result.accounts.map((a) => [a.id, a]));
  if (accounts.size !== result.accounts.length || new Set(result.events.map((e) => e.id)).size !== result.events.length) throw new AppError('INVALID_INPUT');
  for (const account of result.accounts) if (account.connectorId !== connectorId) throw new AppError('FORBIDDEN');
  for (const event of result.events) {
    if (event.connectorId !== connectorId || accounts.get(event.accountId)?.connectorId !== connectorId || event.analysis !== null) throw new AppError('FORBIDDEN');
  }
}

export class ApplicationRuntime {
  readonly events = new EventBus<RuntimeEvents>();
  readonly connectors = new ConnectorRegistry();
  private readonly tasks: TaskManager;
  private readonly now: () => Date;
  private constructor(private readonly storage: Storage, private readonly agent: AgentRuntime, options: { tasks?: TaskManager; now?: () => Date }) {
    this.tasks = options.tasks ?? new SingleFlightTaskManager();
    this.now = options.now ?? (() => new Date());
  }
  static async create(storage: Storage, agent: AgentRuntime, connector: Connector, options: { tasks?: TaskManager; now?: () => Date } = {}): Promise<ApplicationRuntime> {
    const runtime = new ApplicationRuntime(storage, agent, options);
    try {
      storage.recoverInterruptedTasks(runtime.now().toISOString());
      await runtime.connectors.register(connector);
      storage.registerConnector(connector.info);
      return runtime;
    } catch (error) {
      try { await runtime.connectors.dispose(); } finally { storage.close(); }
      throw error;
    }
  }
  snapshot(): Snapshot { return snapshotSchema.parse(this.storage.snapshot()); }
  async sync(connectorId: string): Promise<Snapshot> {
    const connector = this.connectors.get(connectorId, 'account');
    return this.tasks.run('sync', async () => {
      const taskId = randomUUID();
      this.storage.startTask({ id: taskId, connectorId, status: 'running', startedAt: this.now().toISOString(), finishedAt: null, errorCode: null });
      try {
        let result;
        try { result = syncResultSchema.parse(await connector.sync()); }
        catch { throw new AppError('CONNECTOR_FAILED'); }
        validateSyncOwnership(result, connectorId);
        this.storage.commitSync(taskId, connectorId, result, this.now().toISOString());
      } catch (error) {
        this.storage.failTask(taskId, publicError(error).code, this.now().toISOString());
        this.events.emit('sync.failed', { connectorId, taskId });
        throw error;
      }
      this.events.emit('sync.completed', { connectorId, taskId });
      return this.snapshot();
    });
  }
  async analyze(eventId: string): Promise<Snapshot> {
    return this.tasks.run(`analysis:${eventId}`, async () => {
      const stored = this.storage.getEvent(eventId);
      if (!stored) throw new AppError('NOT_FOUND');
      const event: AccountEvent = accountEventSchema.parse(stored);
      let analysis: Analysis;
      try { analysis = analysisSchema.parse(await this.agent.analyze(event)); }
      catch { throw new AppError('AGENT_FAILED'); }
      if (analysis.eventId !== eventId) throw new AppError('AGENT_FAILED');
      this.storage.saveAnalysis(analysis);
      this.events.emit('analysis.completed', analysis);
      return this.snapshot();
    });
  }
  async close(): Promise<void> {
    try { await this.connectors.dispose(); } finally { this.storage.close(); }
  }
}
export * from './credentials';
export * from './accounts';
export * from './sync-engine';
export * from './workspace-runtime';
