import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.errors import AppError, Code
from app.models.entities import Account, BusinessEvent, LegacySecret, SyncTask
from app.services.migration import import_legacy

LEGACY_FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
AT = "2026-11-12T00:00:00.000Z"


def legacy_fixture(path, phase=2):
    old = sqlite3.connect(path)
    old.execute("PRAGMA foreign_keys=ON")
    old.execute("CREATE TABLE _migrations (version INTEGER PRIMARY KEY, hash TEXT NOT NULL)")
    sql = (LEGACY_FIXTURES / "phase0.sql").read_text()
    old.executescript(sql)
    old.execute("INSERT INTO _migrations VALUES(1,?)", (hashlib.sha256(sql.encode()).hexdigest(),))
    old.execute(
        "INSERT INTO connectors VALUES(?,?,?,?,NULL)",
        ("mock", "Mock Connector", '["account","balance","event"]', '["read:mock"]'),
    )
    old.execute(
        "INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?)",
        ("mock:old", "mock", "old", "old", "12.00", "USD", AT, "mock"),
    )
    old.execute(
        "INSERT INTO sync_tasks VALUES(?,?,?,?,?,?)",
        ("old-task", "mock", "succeeded", AT, AT, None),
    )
    old.execute(
        "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "old-event",
            "mock",
            "mock:old",
            "provider-event",
            "balance.observed",
            "12.00",
            "USD",
            AT,
            "mock",
            None,
        ),
    )
    old.commit()
    if phase == 2:
        sql = (LEGACY_FIXTURES / "phase1.sql").read_text()
        old.executescript(sql)
        old.execute(
            "INSERT INTO _migrations VALUES(2,?)", (hashlib.sha256(sql.encode()).hexdigest(),)
        )
        old.execute(
            "UPDATE connections SET status='connected',credential_configured=1,cursor='1',revision=2"
        )
        old.execute(
            "INSERT INTO credential_records VALUES(?,?)",
            ("credential:legacy:mock:old", b"opaque-old-os-ciphertext"),
        )
        old.execute(
            "INSERT INTO sync_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("job-old", "legacy:mock:old", "running", "manual", 1, 0, 3, AT, AT, None, AT, None),
        )
        old.execute(
            "INSERT INTO business_events VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                41,
                "event-old",
                "data.changed",
                "mock",
                "legacy:mock:old",
                AT,
                json.dumps(
                    {
                        "current": {
                            "connectionId": "legacy:mock:old",
                            "providerAccountId": "old",
                            "balance": "12.00",
                            "currency": "USD",
                            "usageUnits": 0,
                            "resourceStatus": "healthy",
                            "revision": 1,
                            "updatedAt": AT,
                            "source": "mock",
                        }
                    }
                ),
                "job-old",
                "data-old",
                None,
            ),
        )
        old.commit()
    old.close()


@pytest.mark.parametrize("phase", [1, 2])
def test_legacy_data_preserved_and_reauthorization_explicit(f, tmp_path, phase):
    source = tmp_path / "source.sqlite"
    legacy_fixture(source, phase)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report = import_legacy(f.services.db, source, tmp_path / "backups")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert report["foreignKeysValid"] and report["keyFieldsVerified"]
    assert report["targetCounts"]["connections"] == 1
    assert report["sourceCounts"]["accounts"] == report["sourceCounts"]["events"] == 1
    assert report["reauthorizationRequired"] == 1
    assert (tmp_path / "backups" / report["backup"]).stat().st_mode & 0o777 == 0o600
    snapshot = f.client.get("/api/v1/snapshot").json()
    assert snapshot["accounts"][0]["balance"] == "12.00"
    assert snapshot["events"][0]["id"] == "old-event"
    assert snapshot["tasks"][0]["id"] == "old-task"
    connection = snapshot["connections"][0]
    assert connection["id"] == "legacy:mock:old"
    assert connection["status"] == "disconnected" and not connection["credentialConfigured"]
    with f.services.db.session() as session:
        assert session.get(Account, connection["id"]).cursor == ("1" if phase == 2 else None)
        if phase == 2:
            assert session.get(SyncTask, "job-old").status == "cancelled"
            assert session.scalar(select(BusinessEvent)).sequence == 41
            assert session.scalar(select(LegacySecret)).ciphertext == b"opaque-old-os-ciphertext"
    assert report["cancelledUnfinishedTasks"] == (1 if phase == 2 else 0)
    # The historical event remains analyzable by the new Python workflow.
    run = f.client.post("/api/v1/agent/analyze", json={"eventId": "old-event"}).json()

    async def execute():
        await f.worker.tick()
        await f.worker.drain()

    asyncio.run(execute())
    assert f.client.get("/api/v1/agent/runs/" + run["id"]).json()["status"] == "succeeded"
    assert f.client.get("/api/v1/snapshot").json()["events"][0]["analysis"]["severity"] == "info"
    with pytest.raises(AppError) as caught:
        import_legacy(f.services.db, source, tmp_path / "backups")
    assert caught.value.code == Code.INVALID_STATE


def test_invalid_legacy_import_rolls_back(f, tmp_path):
    source = tmp_path / "invalid.sqlite"
    legacy_fixture(source)
    with sqlite3.connect(source) as original:
        original.execute("UPDATE business_events SET account_id='missing'")
    with pytest.raises(AppError):
        import_legacy(f.services.db, source, tmp_path / "backups")
    assert f.client.get("/api/v1/accounts").json()["total"] == 0


@pytest.mark.parametrize(
    "field", ["mock", "analysis", "settings", "invalid-json", "unknown-version"]
)
def test_import_rejects_malformed_dtos_before_commit(f, tmp_path, field):
    source = tmp_path / "malformed.sqlite"
    legacy_fixture(source)
    with sqlite3.connect(source) as original:
        if field == "mock":
            original.execute("UPDATE connections SET mock=?", ('{"balance":"not-money"}',))
        elif field == "analysis":
            original.execute(
                "UPDATE business_events SET analysis=?", ('{"secret":"synthetic-value"}',)
            )
        elif field == "invalid-json":
            original.execute("UPDATE connections SET mock=?", ("not-json",))
        elif field == "unknown-version":
            original.execute("UPDATE _migrations SET version=3 WHERE version=2")
        else:
            original.execute("UPDATE app_settings SET value=?", ('{"autoSync":"false"}',))
    with pytest.raises(AppError) as caught:
        import_legacy(f.services.db, source, tmp_path / "backups")
    assert caught.value.code == Code.INVALID_DATA
    assert f.client.get("/api/v1/accounts").json()["total"] == 0
    assert f.client.get("/api/v1/events").json()["items"] == []


def test_historical_sql_fixtures_match_baseline_hashes():
    manifest = json.loads((LEGACY_FIXTURES / "manifest.json").read_text())
    for name, metadata in manifest.items():
        assert (
            hashlib.sha256((LEGACY_FIXTURES / name).read_bytes()).hexdigest() == metadata["sha256"]
        )
