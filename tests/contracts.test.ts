import { describe, expect, it } from 'vitest';
import { connectionSchema, snapshotSchema } from '@tavo/shared';

const at = '2026-11-12T00:00:00.000Z';
const connection = {
  id: 'account-1', connectorId: 'mock', externalAccountId: 'demo-a', displayName: 'Mock A',
  status: 'connected', credentialConfigured: true, lastSyncAt: null, nextSyncAt: null,
  errorCode: null, createdAt: at,
  mock: { balance: '8.50', currency: 'USD', usageUnits: 100, resourceStatus: 'healthy', scenario: 'success' },
};

describe('current client contracts after legacy runtime removal', () => {
  it('rejects credential material in a public connection response', () => {
    expect(connectionSchema.safeParse(connection).success).toBe(true);
    for (const field of ['credentialRef', 'ciphertext', 'credential']) {
      expect(connectionSchema.safeParse({ ...connection, [field]: 'synthetic-private-value' }).success).toBe(false);
    }
  });
  it.each([
    { status: 'connected', credentialConfigured: false },
    { status: 'disconnected', credentialConfigured: true },
    { status: 'expired', errorCode: null },
    { status: 'error', errorCode: null },
  ])('rejects inconsistent account state %#', (invalid) => {
    expect(connectionSchema.safeParse({ ...connection, ...invalid }).success).toBe(false);
  });
  it('keeps imported historical accounts, tasks and analysis readable', () => {
    const analysis = { eventId: 'old-event', severity: 'info', summary: 'Mock summary', evidence: ['Mock evidence'], recommendation: 'Mock recommendation', mode: 'deterministic-mock' };
    const input = {
      connectors: [], connections: [], data: [], jobs: [], businessEvents: [],
      accounts: [{ id: 'old-account', connectorId: 'mock', providerAccountId: 'old', name: 'Imported Mock', balance: '12.00', currency: 'USD', updatedAt: at, source: 'mock' }],
      events: [{ id: 'old-event', connectorId: 'mock', accountId: 'old-account', providerEventId: 'provider-old', kind: 'balance.observed', balance: '12.00', currency: 'USD', occurredAt: at, source: 'mock', analysis }],
      tasks: [{ id: 'old-task', connectorId: 'mock', status: 'succeeded', startedAt: at, finishedAt: at, errorCode: null }],
      settings: { autoSync: false, syncIntervalSeconds: 300, startMinimized: false },
      security: { available: true, backend: 'Fernet keyring (server)', dataLocation: '/server/data' },
    };
    expect(snapshotSchema.parse(input)).toEqual(input);
  });
});
