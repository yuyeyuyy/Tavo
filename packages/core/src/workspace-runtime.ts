import type { ConnectorRegistry } from '@tavo/connectors';
import { AppError, accountEventSchema, snapshotSchema, workspaceRequestSchemas, type AgentRuntime, type BusinessEvent, type CredentialStore, type SecurityStatus, type Snapshot, type WorkspaceOperation, type WorkspaceStorage } from '@tavo/shared';
import { AccountManager } from './accounts';
import { ScopedCredentialAccess } from './credentials';
import { SyncEngine, type SyncEngineOptions } from './sync-engine';
import type { ApplicationRuntime } from './index';

export class WorkspaceRuntime {
  readonly accounts: AccountManager;
  readonly syncEngine: SyncEngine;
  private readonly now: () => Date;
  private closed = false;
  constructor(private readonly base: Pick<ApplicationRuntime, 'snapshot' | 'analyze'>, private readonly repository: WorkspaceStorage, registry: ConnectorRegistry, credentials: CredentialStore, private readonly agent: AgentRuntime, private readonly security: SecurityStatus, options: SyncEngineOptions = {}) {
    this.now = options.now ?? (() => new Date());
    const access = new ScopedCredentialAccess(credentials, repository, registry);
    this.syncEngine = new SyncEngine(repository, registry, access, options);
    this.accounts = new AccountManager(repository, registry, credentials, access, this.syncEngine, options.publish ?? (() => {}), this.now);
  }
  async initialize(startScheduler = true): Promise<void> {
    await this.accounts.recover(); this.syncEngine.recover(); if (startScheduler) this.syncEngine.start();
  }
  snapshot(): Snapshot { return snapshotSchema.parse({ ...this.base.snapshot(), security: this.security }); }
  async execute(operation: WorkspaceOperation, input: unknown): Promise<Snapshot> {
    if (this.closed) throw new AppError('CANCELLED');
    if (!workspaceRequestSchemas[operation].safeParse(input).success) throw new AppError('INVALID_INPUT');
    switch (operation) {
      case 'addConnection': await this.accounts.add(workspaceRequestSchemas.addConnection.parse(input)); break;
      case 'disconnect': await this.accounts.disconnect(workspaceRequestSchemas.disconnect.parse(input).connectionId); break;
      case 'removeConnection': await this.accounts.disconnect(workspaceRequestSchemas.removeConnection.parse(input).connectionId, true); break;
      case 'reconnect': await this.accounts.reconnect(workspaceRequestSchemas.reconnect.parse(input).connectionId); break;
      case 'syncConnection': this.syncEngine.enqueue(workspaceRequestSchemas.syncConnection.parse(input).connectionId); this.syncEngine.tick(); break;
      case 'cancelSync': await this.syncEngine.cancel(workspaceRequestSchemas.cancelSync.parse(input).connectionId); break;
      case 'configureMock': {
        const value = workspaceRequestSchemas.configureMock.parse(input);
        // 同一账户的数据编辑与正在执行的任务互斥，避免游标与配置版本交错。
        await this.syncEngine.cancel(value.connectionId); this.repository.configureMock(value.connectionId, value.config); break;
      }
      case 'updateSettings': this.repository.updateSettings(workspaceRequestSchemas.updateSettings.parse(input), this.now().toISOString()); break;
    }
    return this.snapshot();
  }
  async analyze(eventId: string): Promise<Snapshot> {
    const event = this.repository.events.get(eventId);
    if (!event) return this.base.analyze(eventId);
    if (event.eventType !== 'data.changed' || !event.payload.current) throw new AppError('INVALID_INPUT');
    const data = event.payload.current;
    const analysis = await this.agent.analyze(accountEventSchema.parse({
      id: event.eventId, connectorId: event.sourceId, accountId: event.accountId,
      providerEventId: `revision:${data.revision}`, kind: 'balance.observed', balance: data.balance,
      currency: data.currency, occurredAt: event.occurredAt, source: 'mock', analysis: null,
    }));
    this.repository.saveEventAnalysis(eventId, analysis); return this.snapshot();
  }
  async close(): Promise<void> { this.closed = true; await this.syncEngine.close(); }
}
export type WorkspaceRuntimeEvents = { 'business.persisted': BusinessEvent };
