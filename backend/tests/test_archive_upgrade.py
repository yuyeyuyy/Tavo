import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import SecretStore, initialize_keys
from app.llm.contracts import ModelProfileCreate
from app.llm.keys import ModelKeys
from app.models.legacy import Account, AccountData, Connector, SyncTask
from app.repositories.database import Database, upgrade_database
from app.services.application import Services
from app.services.model_profiles import ModelProfileService
from tests.conftest import Clock


def test_immutable_legacy_sql_fixtures():
    root = Path(__file__).parent / "fixtures" / "legacy"
    manifest = json.loads((root / "manifest.json").read_text())
    for name, entry in manifest.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == entry["sha256"]


def test_upgrade_preserves_every_old_table_model_key_and_does_not_resume_account_queue(tmp_path):
    root = Path(__file__).resolve().parents[1]
    settings = Settings(
        bash_enabled=False,
        data_dir=tmp_path,
        keyring_file=tmp_path / "master.keys",
        client_token_file=tmp_path / "client.token",
        worker_enabled=False,
    )
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.database}")
    command.upgrade(config, "0004_client_model_profiles")
    initialize_keys(settings)
    database = Database(settings.database)
    secrets = SecretStore(database, settings)
    models = ModelProfileService(database, secrets, settings.models_file)
    models.initialize()
    profile = models.create(
        ModelProfileCreate(provider="deepseek", model="fixture-model"), lambda: None
    )
    ModelKeys(secrets).put(models.profile(profile.id), SecretStr("synthetic-upgrade-key"))
    now = Clock().now()
    with database.session(write=True) as db:
        db.add(Connector(id="archive-mock", name="Archive", capabilities=[], permissions=[]))
        db.flush()
        db.add(
            Account(
                id="archive-account",
                connector_id="archive-mock",
                external_account_id="archive",
                display_name="Archive",
                status="connected",
                credential_ref="archive-ref",
                credential_configured=False,
                revision=1,
                mock={},
                created_at=now,
            )
        )
        db.flush()
        db.add(
            AccountData(
                connection_id="archive-account",
                provider_account_id="archive",
                balance="8.50",
                currency="USD",
                usage_units=1,
                resource_status="active",
                revision=1,
                updated_at=now,
                source="mock",
            )
        )
        db.add(
            SyncTask(
                id="archive-pending",
                connection_id="archive-account",
                status="pending",
                trigger="manual",
                attempts=0,
                retry_count=0,
                max_attempts=3,
                created_at=now,
                next_attempt_at=now,
            )
        )
    database.close()

    def snapshot():
        with sqlite3.connect(settings.database) as connection:
            tables = [
                r[0]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')"
                )
            ]
            return {
                name: connection.execute(f'SELECT * FROM "{name}"').fetchall() for name in tables
            }

    before = snapshot()
    upgrade_database(settings.database)
    after = snapshot()
    assert all(after[name] == rows for name, rows in before.items())
    assert len(set(after) - set(before)) == 13
    services = Services(settings)
    asyncio.run(services.initialize())
    try:
        assert (
            ModelKeys(services.secrets).get(services.models.profile(profile.id)).get_secret_value()
            == "synthetic-upgrade-key"
        )
        from app.workers.scheduler import Scheduler

        worker = Scheduler(services)
        assert worker.lead()
        with sqlite3.connect(settings.database) as connection:
            assert connection.execute("SELECT status FROM sync_tasks").fetchone() == ("pending",)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        command.check(config)
    finally:
        asyncio.run(services.close())
