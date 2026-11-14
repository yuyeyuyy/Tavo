from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import AccountOut, AppSettings, DataOut, EventOut, EventPayload, TaskOut
from app.core.errors import AppError, Code
from app.models.entities import (
    Account,
    AccountData,
    BusinessEvent,
    Preferences,
    SyncTask,
    WorkerLease,
)
from app.repositories.time import plus_seconds

ACTIVE = ("pending", "running")


def require_account(session: Session, account_id: str) -> Account:
    row = session.get(Account, account_id)
    if not row or row.deleted_at:
        raise AppError(Code.NOT_FOUND)
    return row


def event_out(row: BusinessEvent) -> EventOut:
    return EventOut.model_validate(row)


class EventRepository:
    def append(
        self,
        session: Session,
        account: Account,
        event_type: str,
        correlation_id: str,
        at: str,
        payload=None,
        dedupe_key=None,
    ):
        key = dedupe_key or f"{correlation_id}:{event_type}"
        existing = session.scalar(
            select(BusinessEvent).where(
                BusinessEvent.account_id == account.id,
                BusinessEvent.event_type == event_type,
                BusinessEvent.dedupe_key == key,
            )
        )
        if existing:
            return existing
        value = EventPayload.model_validate(payload or {}).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        row = BusinessEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            account_id=account.id,
            source_id=account.connector_id,
            occurred_at=at,
            payload=value,
            correlation_id=correlation_id,
            dedupe_key=key,
            analysis=None,
        )
        session.add(row)
        session.flush()
        return row

    def list(
        self,
        session,
        *,
        account_id=None,
        correlation_id=None,
        event_type=None,
        before=None,
        after=None,
        limit=50,
    ):
        query = select(BusinessEvent)
        if account_id:
            query = query.where(BusinessEvent.account_id == account_id)
        if correlation_id:
            query = query.where(BusinessEvent.correlation_id == correlation_id)
        if event_type:
            query = query.where(BusinessEvent.event_type == event_type)
        if before is not None:
            query = query.where(BusinessEvent.sequence < before)
        if after is not None:
            query = query.where(BusinessEvent.sequence > after).order_by(BusinessEvent.sequence)
        else:
            query = query.order_by(BusinessEvent.sequence.desc())
        return list(session.scalars(query.limit(limit)))


class AccountRepository:
    def list(self, session, *, status=None, connector_id=None, limit=50, offset=0):
        query = select(Account).where(Account.deleted_at.is_(None))
        if status:
            query = query.where(Account.status == status)
        if connector_id:
            query = query.where(Account.connector_id == connector_id)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        return list(
            session.scalars(
                query.order_by(Account.created_at, Account.id).limit(limit).offset(offset)
            )
        ), total

    def out(self, account):
        return AccountOut.model_validate(account)


def require_leader(session, owner, fence, at):
    lease = session.get(WorkerLease, 1)
    if not lease or lease.owner != owner or lease.fence != fence or lease.expires_at <= at:
        raise AppError(Code.LEASE_LOST)


def active_task(session, account_id):
    return session.scalar(
        select(SyncTask).where(SyncTask.connection_id == account_id, SyncTask.status.in_(ACTIVE))
    )


def preferences(session) -> AppSettings:
    return AppSettings.model_validate(session.get(Preferences, 1).value)


def commit_sync(session, events, task, result, owner, fence, at):
    require_leader(session, owner, fence, at)
    if task.status != "running" or task.worker_owner != owner or task.worker_fence != fence:
        raise AppError(Code.CANCELLED)
    account = require_account(session, task.connection_id)
    if account.status not in {"connected", "error"} or not account.credential_configured:
        raise AppError(Code.CANCELLED)
    if result.data:
        data = result.data
        if (
            data.connection_id != account.id
            or data.provider_account_id != account.external_account_id
            or data.revision != account.revision
        ):
            raise AppError(Code.INVALID_DATA)
        old = session.get(AccountData, account.id)
        previous = DataOut.model_validate(old) if old else None
        names = {
            "balance": "balance",
            "currency": "currency",
            "usage_units": "usageUnits",
            "resource_status": "resourceStatus",
        }
        changes = [
            alias
            for name, alias in names.items()
            if not previous or getattr(previous, name) != getattr(data, name)
        ]
        if previous and (
            previous.revision > data.revision or (previous.revision == data.revision and changes)
        ):
            raise AppError(Code.INVALID_DATA)
        if old:
            for key, value in data.model_dump().items():
                setattr(old, key, value)
        else:
            session.add(AccountData(**data.model_dump()))
        if changes:
            events.append(
                session,
                account,
                "data.changed",
                task.id,
                at,
                {
                    "previous": previous.model_dump(by_alias=True) if previous else None,
                    "current": data.model_dump(by_alias=True),
                    "changedFields": changes,
                },
                f"revision:{data.revision}",
            )
    account.cursor, account.last_sync_at = result.next_cursor, at
    account.next_sync_at = plus_seconds(at, preferences(session).sync_interval_seconds)
    account.status, account.error_code = "connected", None
    task.status, task.finished_at, task.error_code = "succeeded", at, None
    events.append(session, account, "sync.completed", task.id, at, {"attempt": task.attempts})
    session.flush()
    return TaskOut.model_validate(task)
