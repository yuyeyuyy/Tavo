import { randomBytes, randomUUID } from 'node:crypto';
import type { ConnectorRegistry } from '@tavo/connectors';
import { AppError, publicError, workspaceRequestSchemas, type BusinessEvent, type ConnectionStatus, type CredentialAccess, type CredentialStore, type WorkspaceStorage } from '@tavo/shared';
import type { SyncEngine } from './sync-engine';

const transitions: Record<ConnectionStatus, readonly ConnectionStatus[]> = {
  disconnected: ['connecting'], connecting: ['connected', 'error', 'disconnected'],
  connected: ['connecting', 'expired', 'error', 'disconnected'], expired: ['connecting', 'disconnected'],
  error: ['connecting', 'connected', 'expired', 'disconnected'],
};
export function assertConnectionTransition(from: ConnectionStatus, to: ConnectionStatus): void {
  if (from !== to && !transitions[from].includes(to)) throw new AppError('INVALID_STATE');
}
export class AccountManager {
  private readonly busy = new Set<string>();
  constructor(private readonly repository: WorkspaceStorage, private readonly registry: ConnectorRegistry, private readonly credentials: Pick<CredentialStore, 'set' | 'delete'>, private readonly access: CredentialAccess, private readonly engine: SyncEngine, private readonly onPublish: (events: BusinessEvent[]) => void, private readonly now: () => Date = () => new Date(), private readonly makeSecret = () => `mock-only:${randomBytes(32).toString('hex')}`) {}
  private publish(events: BusinessEvent[]) { try { this.onPublish(events); } catch { /* 观察者不能回滚连接或同步状态。 */ } }
  private require(id: string) { const connection = this.repository.getConnection(id); if (!connection || connection.deletedAt) throw new AppError('NOT_FOUND'); return connection; }
  private async exclusive<T>(id: string, operation: () => Promise<T>): Promise<T> {
    if (this.busy.has(id)) throw new AppError('BUSY'); this.busy.add(id);
    try { return await operation(); } finally { this.busy.delete(id); }
  }
  async add(input: unknown): Promise<string> {
    const parsed = workspaceRequestSchemas.addConnection.safeParse(input); if (!parsed.success) throw new AppError('INVALID_INPUT');
    const value = parsed.data; this.registry.get(value.connectorId, 'account');
    const id = randomUUID();
    this.repository.addConnection({
      id, connectorId: value.connectorId, externalAccountId: value.externalAccountId, displayName: value.displayName,
      status: 'connecting', credentialRef: `credential:${id}`, credentialConfigured: false,
      lastSyncAt: null, nextSyncAt: null, errorCode: null, createdAt: this.now().toISOString(), cursor: null, revision: 1,
      mock: { balance: '8.50', currency: 'USD', usageUnits: 100, resourceStatus: 'healthy', scenario: 'success' },
      cleanupPending: false, deleteRequested: false, deletedAt: null,
    });
    await this.reconnect(id); return id;
  }
  async reconnect(id: string): Promise<void> {
    return this.exclusive(id, async () => {
      const connection = this.require(id); assertConnectionTransition(connection.status, 'connecting');
      await this.engine.cancel(id); this.repository.setConnecting(id);
      try {
        if (connection.cleanupPending) { await this.credentials.delete(connection.credentialRef); this.repository.finishCleanup(id, this.now().toISOString()); }
        await this.credentials.set(connection.credentialRef, this.makeSecret());
        await this.access.use(this.require(id), async (secret) => { if (!secret.startsWith('mock-only:')) throw new AppError('AUTH_EXPIRED'); });
        this.publish(this.repository.finishConnecting(id, this.now().toISOString()));
      } catch (error) {
        this.repository.connectionError(id, publicError(error).code); throw error;
      }
    });
  }
  async disconnect(id: string, remove = false): Promise<void> {
    return this.exclusive(id, async () => {
      const connection = this.require(id); assertConnectionTransition(connection.status, 'disconnected');
      this.publish(this.repository.beginDisconnect(id, remove, this.now().toISOString()));
      await this.engine.cancel(id);
      await this.credentials.delete(connection.credentialRef);
      this.repository.finishCleanup(id, this.now().toISOString());
    });
  }
  async recover(): Promise<void> {
    for (const connection of this.repository.listConnections()) {
      if (connection.cleanupPending) { await this.credentials.delete(connection.credentialRef); this.repository.finishCleanup(connection.id, this.now().toISOString()); }
      else if (connection.status === 'connecting') {
        await this.credentials.delete(connection.credentialRef); this.repository.connectionError(connection.id, 'INTERRUPTED');
      }
    }
  }
}
