
CREATE TABLE connectors (
  id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, capabilities TEXT NOT NULL,
  permissions TEXT NOT NULL, last_sync_at TEXT
);
CREATE TABLE accounts (
  id TEXT PRIMARY KEY NOT NULL, connector_id TEXT NOT NULL REFERENCES connectors(id),
  provider_account_id TEXT NOT NULL, name TEXT NOT NULL, balance TEXT NOT NULL,
  currency TEXT NOT NULL CHECK(currency IN ('USD', 'CNY')), updated_at TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source = 'mock')
);
CREATE UNIQUE INDEX accounts_provider_unique ON accounts(connector_id, provider_account_id);
CREATE UNIQUE INDEX accounts_owner_unique ON accounts(id, connector_id);
CREATE TABLE sync_tasks (
  id TEXT PRIMARY KEY NOT NULL, connector_id TEXT NOT NULL REFERENCES connectors(id),
  status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed', 'interrupted')),
  started_at TEXT NOT NULL, finished_at TEXT, error_code TEXT
);
CREATE TABLE events (
  id TEXT PRIMARY KEY NOT NULL, connector_id TEXT NOT NULL REFERENCES connectors(id),
  account_id TEXT NOT NULL, provider_event_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind = 'balance.observed'), balance TEXT NOT NULL,
  currency TEXT NOT NULL CHECK(currency IN ('USD', 'CNY')), occurred_at TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source = 'mock'), analysis TEXT,
  FOREIGN KEY(account_id, connector_id) REFERENCES accounts(id, connector_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX events_provider_unique ON events(connector_id, provider_event_id);
