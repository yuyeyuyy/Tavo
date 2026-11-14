import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.schemas import (
    AccountOut,
    AppSettings,
    DataOut,
    EventOut,
    LegacyAccount,
    LegacyEvent,
    LegacyTask,
    MockConfig,
    TaskOut,
)
from app.core.config import utcnow
from app.core.errors import AppError, Code
from app.models.entities import (
    Account,
    AccountData,
    BusinessEvent,
    Connector,
    ImportRecord,
    LegacyRecord,
    LegacySecret,
    Preferences,
    SyncTask,
)


def backup_legacy(source: Path, directory: Path) -> Path:
    source = source.resolve(strict=True)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / f"legacy-{uuid4().hex}.sqlite"
    try:
        with (
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original,
            sqlite3.connect(target) as backup,
        ):
            original.backup(backup)
        os.chmod(target, 0o600)
        return target
    except sqlite3.Error:
        raise AppError(Code.STORAGE_FAILED) from None


def decoded(row):
    value = dict(row)
    for key in ("capabilities", "permissions", "mock", "payload", "analysis", "value"):
        if key in value and isinstance(value[key], str):
            value[key] = json.loads(value[key])
    return value


def import_legacy(database, source: Path, backup_dir: Path):
    try:
        return _import_legacy(database, source, backup_dir)
    except (ValidationError, ValueError, KeyError, TypeError):
        raise AppError(Code.INVALID_DATA) from None
    except OSError:
        raise AppError(Code.STORAGE_FAILED) from None


def _import_legacy(database, source: Path, backup_dir: Path):
    if source.resolve() == database.path.resolve():
        raise AppError(Code.INVALID_INPUT)
    backup = backup_legacy(source, backup_dir)
    old = sqlite3.connect(backup)
    old.row_factory = sqlite3.Row
    try:
        names = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if (
            "_migrations" not in names
            or {r[0] for r in old.execute("SELECT version FROM _migrations")} not in ({1}, {1, 2})
            or old.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise AppError(Code.INVALID_DATA)
        accepted = (
            "connectors",
            "accounts",
            "events",
            "sync_tasks",
            "connections",
            "connection_data",
            "sync_jobs",
            "business_events",
            "credential_records",
            "app_settings",
            "_migrations",
        )
        records = {
            name: [dict(row) for row in old.execute(f'SELECT * FROM "{name}"')]
            for name in accepted
            if name in names
        }
    except (sqlite3.Error, ValueError):
        raise AppError(Code.INVALID_DATA) from None
    finally:
        old.close()
    report = {
        "sourceCounts": {name: len(rows) for name, rows in records.items()},
        "sourceSha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        "reauthorizationRequired": 0,
        "cancelledUnfinishedTasks": 0,
        "backup": backup.name,
    }
    now = utcnow()
    with database.session(write=True) as session:
        if session.scalar(select(func.count()).select_from(Account)) or session.scalar(
            select(func.count()).select_from(ImportRecord)
        ):
            raise AppError(Code.INVALID_STATE)
        for original in records.get("connectors", []):
            row = decoded(original)
            if not session.get(Connector, row["id"]):
                session.add(
                    Connector(
                        id=row["id"],
                        name=row["name"],
                        capabilities=row["capabilities"],
                        permissions=row["permissions"],
                    )
                )
        session.flush()
        connections = {}
        for original in records.get("connections", []):
            row = decoded(original)
            account = Account(
                id=row["id"],
                connector_id=row["connector_id"],
                external_account_id=row["external_account_id"],
                display_name=row["display_name"],
                status="disconnected",
                credential_ref=row["credential_ref"],
                credential_configured=False,
                cursor=row["cursor"],
                revision=row["revision"],
                mock=row["mock"],
                last_sync_at=row["last_sync_at"],
                next_sync_at=None,
                error_code=None,
                created_at=row["created_at"],
                deleted_at=row["deleted_at"],
            )
            session.add(account)
            connections[account.id] = account
            report["reauthorizationRequired"] += int(not account.deleted_at)
        session.flush()
        legacy_map = {}
        for original in records.get("accounts", []):
            row = decoded(original)
            match = next(
                (
                    r
                    for r in connections.values()
                    if r.connector_id == row["connector_id"]
                    and r.external_account_id == row["provider_account_id"]
                ),
                None,
            )
            if match is None:
                account_id = "legacy:" + row["id"]
                match = Account(
                    id=account_id,
                    connector_id=row["connector_id"],
                    external_account_id=row["provider_account_id"],
                    display_name=row["name"],
                    status="disconnected",
                    credential_ref="credential:" + account_id,
                    credential_configured=False,
                    cursor=None,
                    revision=1,
                    mock=MockConfig(
                        balance=row["balance"], currency=row["currency"], usage_units=0
                    ).model_dump(by_alias=True),
                    last_sync_at=row["updated_at"],
                    next_sync_at=None,
                    error_code=None,
                    created_at=row["updated_at"],
                    deleted_at=None,
                )
                session.add(match)
                connections[account_id] = match
                report["reauthorizationRequired"] += 1
            legacy_map[row["id"]] = match.id
        session.flush()
        for original in records.get("connection_data", []):
            row = decoded(original)
            session.add(AccountData(**row))
        session.flush()
        for original in records.get("accounts", []):
            row = decoded(original)
            account_id = legacy_map[row["id"]]
            if not session.get(AccountData, account_id):
                session.add(
                    AccountData(
                        connection_id=account_id,
                        provider_account_id=row["provider_account_id"],
                        balance=row["balance"],
                        currency=row["currency"],
                        usage_units=0,
                        resource_status="healthy",
                        revision=1,
                        updated_at=row["updated_at"],
                        source=row["source"],
                    )
                )
        for original in records.get("sync_jobs", []):
            row = decoded(original)
            if row["status"] in {"pending", "running"}:
                row["status"], row["error_code"], row["finished_at"] = (
                    "cancelled",
                    Code.CANCELLED,
                    now,
                )
                report["cancelledUnfinishedTasks"] += 1
            session.add(SyncTask(**row, worker_owner=None, worker_fence=None))
        for original in sorted(records.get("business_events", []), key=lambda row: row["sequence"]):
            session.add(BusinessEvent(**decoded(original)))
        for original in records.get("credential_records", []):
            session.add(
                LegacySecret(
                    reference=original["reference"],
                    ciphertext=original["ciphertext"],
                    scheme="electron-safeStorage",
                )
            )
        for name in ("accounts", "events", "sync_tasks", "_migrations", "connections"):
            for original in records.get(name, []):
                row = decoded(original)
                connection_id = (
                    legacy_map.get(row.get("account_id"))
                    if name == "events"
                    else legacy_map.get(row.get("id"))
                    if name == "accounts"
                    else row.get("id")
                    if name == "connections"
                    else None
                )
                session.add(
                    LegacyRecord(
                        table_name=name,
                        record_id=str(row.get("id", row.get("version"))),
                        data=row,
                        connection_id=connection_id,
                    )
                )
        for original in records.get("app_settings", []):
            row = decoded(original)
            existing = session.get(Preferences, 1)
            if existing:
                existing.value = row["value"]
            else:
                session.add(Preferences(id=1, value=row["value"]))
        session.flush()
        # Imported files are untrusted inputs too. Validate DTOs before committing,
        # so malformed JSON cannot make a successful import unreadable by clients.
        try:
            for model, dto in (
                (Account, AccountOut),
                (AccountData, DataOut),
                (SyncTask, TaskOut),
                (BusinessEvent, EventOut),
            ):
                for value in session.scalars(select(model)):
                    dto.model_validate(value)
            legacy_types = {
                "accounts": LegacyAccount,
                "events": LegacyEvent,
                "sync_tasks": LegacyTask,
            }
            for value in session.scalars(select(LegacyRecord)):
                if value.table_name in legacy_types:
                    legacy_types[value.table_name].model_validate(value.data)
            config = session.get(Preferences, 1)
            if config:
                AppSettings.model_validate(config.value)
        except ValidationError:
            raise AppError(Code.INVALID_DATA) from None
        if session.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall():
            raise AppError(Code.INVALID_DATA)
        for original in records.get("connections", []):
            migrated = session.get(Account, original["id"])
            if (
                migrated.connector_id != original["connector_id"]
                or migrated.external_account_id != original["external_account_id"]
                or migrated.display_name != original["display_name"]
            ):
                raise AppError(Code.INVALID_DATA)
        for original in records.get("business_events", []):
            migrated = session.get(BusinessEvent, original["sequence"])
            if (
                migrated.event_id != original["event_id"]
                or migrated.correlation_id != original["correlation_id"]
                or migrated.payload != decoded(original)["payload"]
            ):
                raise AppError(Code.INVALID_DATA)
        report["targetCounts"] = {
            "connections": session.scalar(select(func.count()).select_from(Account)),
            "data": session.scalar(select(func.count()).select_from(AccountData)),
            "jobs": session.scalar(select(func.count()).select_from(SyncTask)),
            "businessEvents": session.scalar(select(func.count()).select_from(BusinessEvent)),
            "legacySecrets": session.scalar(select(func.count()).select_from(LegacySecret)),
            "legacyRecords": session.scalar(select(func.count()).select_from(LegacyRecord)),
        }
        if report["targetCounts"]["jobs"] != len(records.get("sync_jobs", [])) or report[
            "targetCounts"
        ]["businessEvents"] != len(records.get("business_events", [])):
            raise AppError(Code.INVALID_DATA)
        report["foreignKeysValid"], report["keyFieldsVerified"] = True, True
        session.add(ImportRecord(id=str(uuid4()), report=report))
    return report
