import { integer, sqliteTable, text, uniqueIndex, foreignKey } from 'drizzle-orm/sqlite-core';
import type { Analysis, Capability } from '@tavo/shared';

export const connectors = sqliteTable('connectors', {
  id: text('id').primaryKey(), name: text('name').notNull(),
  capabilities: text('capabilities', { mode: 'json' }).$type<Capability[]>().notNull(),
  permissions: text('permissions', { mode: 'json' }).$type<['read:mock']>().notNull(),
  lastSyncAt: text('last_sync_at'),
});
export const accounts = sqliteTable('accounts', {
  id: text('id').primaryKey(), connectorId: text('connector_id').notNull().references(() => connectors.id),
  providerAccountId: text('provider_account_id').notNull(), name: text('name').notNull(),
  balance: text('balance').notNull(), currency: text('currency', { enum: ['USD', 'CNY'] }).notNull(),
  updatedAt: text('updated_at').notNull(), source: text('source', { enum: ['mock'] }).notNull(),
}, (table) => [uniqueIndex('accounts_provider_unique').on(table.connectorId, table.providerAccountId), uniqueIndex('accounts_owner_unique').on(table.id, table.connectorId)]);
export const syncTasks = sqliteTable('sync_tasks', {
  id: text('id').primaryKey(), connectorId: text('connector_id').notNull().references(() => connectors.id),
  status: text('status', { enum: ['running', 'succeeded', 'failed', 'interrupted'] }).notNull(),
  startedAt: text('started_at').notNull(), finishedAt: text('finished_at'), errorCode: text('error_code'),
});
export const events = sqliteTable('events', {
  id: text('id').primaryKey(), connectorId: text('connector_id').notNull().references(() => connectors.id),
  accountId: text('account_id').notNull(), providerEventId: text('provider_event_id').notNull(),
  kind: text('kind', { enum: ['balance.observed'] }).notNull(), balance: text('balance').notNull(),
  currency: text('currency', { enum: ['USD', 'CNY'] }).notNull(), occurredAt: text('occurred_at').notNull(),
  source: text('source', { enum: ['mock'] }).notNull(), analysis: text('analysis', { mode: 'json' }).$type<Analysis>(),
}, (table) => [
  uniqueIndex('events_provider_unique').on(table.connectorId, table.providerEventId),
  foreignKey({ columns: [table.accountId, table.connectorId], foreignColumns: [accounts.id, accounts.connectorId] }).onDelete('cascade'),
]);
export const migrations = sqliteTable('_migrations', {
  version: integer('version').primaryKey(), hash: text('hash').notNull(),
});
