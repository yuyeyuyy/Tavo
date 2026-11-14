
CREATE TABLE connections (
 id TEXT PRIMARY KEY NOT NULL, connector_id TEXT NOT NULL REFERENCES connectors(id),
 external_account_id TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL
 CHECK(status IN ('disconnected','connecting','connected','expired','error')),
 credential_ref TEXT NOT NULL UNIQUE, credential_configured INTEGER NOT NULL DEFAULT 0,
 cursor TEXT, revision INTEGER NOT NULL DEFAULT 1, mock TEXT NOT NULL,
 last_sync_at TEXT, next_sync_at TEXT, error_code TEXT, created_at TEXT NOT NULL,
 cleanup_pending INTEGER NOT NULL DEFAULT 0, delete_requested INTEGER NOT NULL DEFAULT 0, deleted_at TEXT
);
CREATE UNIQUE INDEX connections_active_identity ON connections(connector_id, external_account_id) WHERE deleted_at IS NULL;
CREATE TABLE connection_data (
 connection_id TEXT PRIMARY KEY NOT NULL REFERENCES connections(id), provider_account_id TEXT NOT NULL,
 balance TEXT NOT NULL, currency TEXT NOT NULL, usage_units INTEGER NOT NULL, resource_status TEXT NOT NULL,
 revision INTEGER NOT NULL, updated_at TEXT NOT NULL, source TEXT NOT NULL CHECK(source='mock')
);
CREATE TABLE sync_jobs (
 id TEXT PRIMARY KEY NOT NULL, connection_id TEXT NOT NULL REFERENCES connections(id),
 status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','cancelled')),
 trigger TEXT NOT NULL, attempts INTEGER NOT NULL, retry_count INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, next_attempt_at TEXT NOT NULL, error_code TEXT
);
CREATE UNIQUE INDEX sync_jobs_active_account ON sync_jobs(connection_id) WHERE status IN ('pending','running');
CREATE INDEX sync_jobs_due ON sync_jobs(status,next_attempt_at);
CREATE TABLE business_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL,
 source_id TEXT NOT NULL, account_id TEXT NOT NULL, occurred_at TEXT NOT NULL, payload TEXT NOT NULL,
 correlation_id TEXT NOT NULL, dedupe_key TEXT NOT NULL, analysis TEXT,
 UNIQUE(account_id,event_type,dedupe_key)
);
CREATE TABLE credential_records (reference TEXT PRIMARY KEY NOT NULL, ciphertext BLOB NOT NULL);
CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
INSERT INTO app_settings VALUES(1,'{"autoSync":false,"syncIntervalSeconds":300,"startMinimized":false}');
INSERT INTO connections
 (id,connector_id,external_account_id,display_name,status,credential_ref,mock,last_sync_at,created_at)
 SELECT 'legacy:'||id,connector_id,provider_account_id,name,'disconnected','credential:legacy:'||id,
 json_object('balance',balance,'currency',currency,'usageUnits',0,'resourceStatus','healthy','scenario','success'),
 updated_at,updated_at FROM accounts;
INSERT INTO connection_data SELECT 'legacy:'||id,provider_account_id,balance,currency,0,'healthy',1,updated_at,'mock' FROM accounts;
