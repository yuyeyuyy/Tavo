import { blob, integer, sqliteTable, text, uniqueIndex } from 'drizzle-orm/sqlite-core';
import { sql } from 'drizzle-orm';
import type { Analysis, BusinessEvent, ConnectionStatus, ErrorCode, MockConfig, Settings, SyncJob } from '@tavo/shared';
import { connectors } from './schema';

export const connections = sqliteTable('connections', {
  id: text('id').primaryKey(), connectorId: text('connector_id').notNull().references(() => connectors.id),
  externalAccountId: text('external_account_id').notNull(), displayName: text('display_name').notNull(),
  status: text('status').$type<ConnectionStatus>().notNull(), credentialRef: text('credential_ref').notNull().unique(),
  credentialConfigured: integer('credential_configured', { mode: 'boolean' }).notNull(), cursor: text('cursor'),
  revision: integer('revision').notNull(), mock: text('mock', { mode: 'json' }).$type<MockConfig>().notNull(),
  lastSyncAt: text('last_sync_at'), nextSyncAt: text('next_sync_at'), errorCode: text('error_code').$type<ErrorCode>(),
  createdAt: text('created_at').notNull(), cleanupPending: integer('cleanup_pending', { mode: 'boolean' }).notNull(),
  deleteRequested: integer('delete_requested', { mode: 'boolean' }).notNull(), deletedAt: text('deleted_at'),
}, (t) => [uniqueIndex('connections_active_identity').on(t.connectorId, t.externalAccountId).where(sql`${t.deletedAt} IS NULL`)]);
export const connectionData = sqliteTable('connection_data', {
  connectionId: text('connection_id').primaryKey().references(() => connections.id), providerAccountId: text('provider_account_id').notNull(),
  balance: text('balance').notNull(), currency: text('currency', { enum: ['USD', 'CNY'] }).notNull(),
  usageUnits: integer('usage_units').notNull(), resourceStatus: text('resource_status', { enum: ['healthy', 'degraded'] }).notNull(),
  revision: integer('revision').notNull(), updatedAt: text('updated_at').notNull(), source: text('source', { enum: ['mock'] }).notNull(),
});
export const syncJobs = sqliteTable('sync_jobs', {
  id: text('id').primaryKey(), connectionId: text('connection_id').notNull().references(() => connections.id),
  status: text('status').$type<SyncJob['status']>().notNull(), trigger: text('trigger').$type<SyncJob['trigger']>().notNull(),
  attempts: integer('attempts').notNull(), retryCount: integer('retry_count').notNull(), maxAttempts: integer('max_attempts').notNull(),
  createdAt: text('created_at').notNull(), startedAt: text('started_at'), finishedAt: text('finished_at'),
  nextAttemptAt: text('next_attempt_at').notNull(), errorCode: text('error_code').$type<ErrorCode>(),
}, (t) => [uniqueIndex('sync_jobs_active_account').on(t.connectionId).where(sql`${t.status} IN ('pending','running')`)]);
export const businessEvents = sqliteTable('business_events', {
  sequence: integer('sequence').primaryKey({ autoIncrement: true }), eventId: text('event_id').notNull().unique(),
  eventType: text('event_type').$type<BusinessEvent['eventType']>().notNull(), sourceId: text('source_id').notNull(),
  accountId: text('account_id').notNull(), occurredAt: text('occurred_at').notNull(),
  payload: text('payload', { mode: 'json' }).$type<BusinessEvent['payload']>().notNull(), correlationId: text('correlation_id').notNull(),
  dedupeKey: text('dedupe_key').notNull(), analysis: text('analysis', { mode: 'json' }).$type<Analysis>(),
}, (t) => [uniqueIndex('business_event_dedupe').on(t.accountId, t.eventType, t.dedupeKey)]);
export const credentialRecords = sqliteTable('credential_records', {
  reference: text('reference').primaryKey(), ciphertext: blob('ciphertext', { mode: 'buffer' }).notNull(),
});
export const appSettings = sqliteTable('app_settings', {
  id: integer('id').primaryKey(), value: text('value', { mode: 'json' }).$type<Settings>().notNull(),
});
