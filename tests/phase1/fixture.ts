import { randomBytes } from 'node:crypto';
import { ApplicationRuntime, EncryptedCredentialStore, WorkspaceRuntime, type EncryptionProvider, type SyncEngineOptions } from '@tavo/core';
import { MockConnector } from '@tavo/connectors';
import { MockAgentRuntime } from '@tavo/agent';
import { SqliteStorage } from '@tavo/storage';
import type { BusinessEvent } from '@tavo/shared';

// 仅测试替身：用随机密文索引保存内存映射，不宣称提供真实加密。
// 桌面 E2E 另行使用系统 safeStorage 验证实际加密与重启解密。
export class TestEncryptionProvider implements EncryptionProvider {
  available = true; shouldReEncrypt = false;
  private readonly values = new Map<string, string>();
  async status() { return { available: this.available, backend: 'test-double' }; }
  async encrypt(secret: string) { const bytes = randomBytes(32); this.values.set(bytes.toString('hex'), secret); return bytes; }
  async decrypt(ciphertext: Uint8Array) { const result = this.values.get(Buffer.from(ciphertext).toString('hex')); if (!result) throw new Error('synthetic decryption failure'); return { result, shouldReEncrypt: this.shouldReEncrypt }; }
}
export async function fixture(options: SyncEngineOptions = {}, path = ':memory:', provider = new TestEncryptionProvider()) {
  let instant = Date.parse('2026-11-13T00:00:00.000Z');
  const now = options.now ?? (() => new Date(instant));
  const storage = new SqliteStorage(path); const connector = new MockConnector(now, 0); const agent = new MockAgentRuntime();
  const base = await ApplicationRuntime.create(storage, agent, connector, { now });
  const credentials = new EncryptedCredentialStore(provider, storage.workspace.ciphertexts);
  const published: BusinessEvent[] = [];
  const runtime = new WorkspaceRuntime(base, storage.workspace, base.connectors, credentials, agent,
    { available: true, backend: 'test-double', dataLocation: 'isolated test database' },
    { random: () => 0, timeoutMs: 50, ...options, now, publish: (events) => { published.push(...events); options.publish?.(events); } });
  await runtime.initialize(false);
  return {
    storage, connector, agent, base, provider, credentials, runtime, published, now,
    advance: (ms: number) => { instant += ms; },
    add: async (externalAccountId = 'demo-a', displayName = '测试账户') => {
      await runtime.execute('addConnection', { connectorId: 'mock', externalAccountId, displayName });
      return storage.workspace.listConnections().find((c) => c.externalAccountId === externalAccountId)!;
    },
    sync: async (id: string) => { const job = runtime.syncEngine.enqueue(id); runtime.syncEngine.tick(); await runtime.syncEngine.settle(); return storage.workspace.getJob(job.id)!; },
    close: async () => { await runtime.close(); await base.close(); },
  };
}
export type Fixture = Awaited<ReturnType<typeof fixture>>;
