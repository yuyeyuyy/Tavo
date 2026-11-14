from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Connector(Base):
    __tablename__ = "connectors"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    capabilities: Mapped[list[str]] = mapped_column(JSON)
    permissions: Mapped[list[str]] = mapped_column(JSON)


class Account(Base):
    __tablename__ = "account_connections"
    __table_args__ = (
        Index(
            "account_active_identity",
            "connector_id",
            "external_account_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"))
    external_account_id: Mapped[str]
    display_name: Mapped[str]
    status: Mapped[str]
    credential_ref: Mapped[str] = mapped_column(unique=True)
    credential_configured: Mapped[bool] = mapped_column(Boolean)
    cursor: Mapped[str | None]
    revision: Mapped[int]
    mock: Mapped[dict[str, Any]] = mapped_column(JSON)
    last_sync_at: Mapped[str | None]
    next_sync_at: Mapped[str | None]
    error_code: Mapped[str | None]
    created_at: Mapped[str]
    deleted_at: Mapped[str | None]


class AccountData(Base):
    __tablename__ = "account_data"
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("account_connections.id"), primary_key=True
    )
    provider_account_id: Mapped[str]
    balance: Mapped[str]
    currency: Mapped[str]
    usage_units: Mapped[int]
    resource_status: Mapped[str]
    revision: Mapped[int]
    updated_at: Mapped[str]
    source: Mapped[str]


class SyncTask(Base):
    __tablename__ = "sync_tasks"
    __table_args__ = (
        Index(
            "task_active_account",
            "connection_id",
            unique=True,
            sqlite_where=text("status IN ('pending','running')"),
        ),
        Index("task_due", "status", "next_attempt_at"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("account_connections.id"))
    status: Mapped[str]
    trigger: Mapped[str]
    attempts: Mapped[int]
    retry_count: Mapped[int]
    max_attempts: Mapped[int]
    created_at: Mapped[str]
    started_at: Mapped[str | None]
    finished_at: Mapped[str | None]
    next_attempt_at: Mapped[str]
    error_code: Mapped[str | None]
    worker_owner: Mapped[str | None]
    worker_fence: Mapped[int | None]


class BusinessEvent(Base):
    __tablename__ = "business_events"
    __table_args__ = (
        UniqueConstraint("account_id", "event_type", "dedupe_key"),
        {"sqlite_autoincrement": True},
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(unique=True)
    event_type: Mapped[str]
    source_id: Mapped[str]
    account_id: Mapped[str] = mapped_column(ForeignKey("account_connections.id"))
    occurred_at: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    correlation_id: Mapped[str]
    dedupe_key: Mapped[str]
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Secret(Base):
    __tablename__ = "secrets"
    reference: Mapped[str] = mapped_column(String, primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str]


class LegacySecret(Base):
    __tablename__ = "legacy_secrets"
    reference: Mapped[str] = mapped_column(String, primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    scheme: Mapped[str] = mapped_column(default="electron-safeStorage")


class Preferences(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class LegacyRecord(Base):
    __tablename__ = "legacy_records"
    __table_args__ = (UniqueConstraint("table_name", "record_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str]
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("account_connections.id"))
    record_id: Mapped[str]
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class ImportRecord(Base):
    __tablename__ = "import_records"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index(
            "agent_active_event",
            "event_id",
            unique=True,
            sqlite_where=text("status IN ('pending','running')"),
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str]
    account_id: Mapped[str] = mapped_column(ForeignKey("account_connections.id"))
    status: Mapped[str]
    progress: Mapped[int]
    created_at: Mapped[str]
    started_at: Mapped[str | None]
    finished_at: Mapped[str | None]
    error_code: Mapped[str | None]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    worker_owner: Mapped[str | None]
    worker_fence: Mapped[int | None]


class WorkerLease(Base):
    __tablename__ = "worker_lease"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str]
    fence: Mapped[int]
    expires_at: Mapped[str]


class ClientSession(Base):
    __tablename__ = "client_sessions"
    digest: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[str]
    owner_fingerprint: Mapped[str]
