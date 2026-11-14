import secrets
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import delete, select

from app.api.schemas import AccountOut, AccountPage, AddAccount, AppSettings, MockConfig, TaskOut
from app.core.config import utcnow
from app.core.errors import AppError, Code
from app.models.entities import Account, AccountData, Preferences, Secret, SyncTask
from app.repositories.time import plus_seconds
from app.repositories.workspace import (
    ACTIVE,
    AccountRepository,
    active_task,
    preferences,
    require_account,
)


def assert_transition(source, target):
    transitions = {
        "disconnected": {"connecting"},
        "connecting": {"connected", "error", "disconnected"},
        "connected": {"connecting", "expired", "error", "disconnected"},
        "expired": {"connecting", "disconnected"},
        "error": {"connecting", "connected", "expired", "disconnected"},
    }
    if source != target and target not in transitions[source]:
        raise AppError(Code.INVALID_STATE)


class AccountService:
    def __init__(self, db, registry, secret_store, events, now=utcnow):
        self.db, self.registry, self.secrets, self.events, self.now = (
            db,
            registry,
            secret_store,
            events,
            now,
        )
        self.repository = AccountRepository()

    def list(self, **filters):
        with self.db.session() as session:
            rows, total = self.repository.list(session, **filters)
            return AccountPage(
                items=[AccountOut.model_validate(row) for row in rows],
                total=total,
                limit=filters.get("limit", 50),
                offset=filters.get("offset", 0),
            )

    def get(self, account_id):
        with self.db.session() as session:
            return AccountOut.model_validate(require_account(session, account_id))

    def add(self, request: AddAccount):
        self.registry.get(request.connector_id)
        with self.db.session(write=True) as session:
            if session.scalar(
                select(Account).where(
                    Account.deleted_at.is_(None),
                    Account.connector_id == request.connector_id,
                    Account.external_account_id == request.external_account_id,
                )
            ):
                raise AppError(Code.DUPLICATE_CONNECTION)
            account_id = str(uuid4())
            row = Account(
                id=account_id,
                connector_id=request.connector_id,
                external_account_id=request.external_account_id,
                display_name=request.display_name,
                status="connecting",
                credential_ref=f"credential:{account_id}",
                credential_configured=False,
                cursor=None,
                revision=1,
                mock=MockConfig().model_dump(by_alias=True),
                last_sync_at=None,
                next_sync_at=None,
                error_code=None,
                created_at=self.now(),
                deleted_at=None,
            )
            session.add(row)
            session.flush()
            self._connect(session, row)
            return AccountOut.model_validate(row)

    def _connect(self, session, row):
        assert_transition(row.status, "connecting")
        self.registry.get(row.connector_id)
        self._cancel(session, row)
        row.status = "connecting"
        self.secrets.put(
            session, row.credential_ref, SecretStr("mock-only:" + secrets.token_urlsafe(32))
        )
        row.status, row.credential_configured, row.error_code = "connected", True, None
        row.next_sync_at = plus_seconds(self.now(), preferences(session).sync_interval_seconds)
        self.events.append(session, row, "account.connected", str(uuid4()), self.now())

    def reconnect(self, account_id):
        with self.db.session(write=True) as session:
            row = require_account(session, account_id)
            self._connect(session, row)
            return AccountOut.model_validate(row)

    def _cancel(self, session, account):
        for job in session.scalars(
            select(SyncTask).where(
                SyncTask.connection_id == account.id, SyncTask.status.in_(ACTIVE)
            )
        ):
            job.status, job.error_code, job.finished_at = "cancelled", Code.CANCELLED, self.now()
            self.events.append(
                session,
                account,
                "sync.cancelled",
                job.id,
                self.now(),
                {"errorCode": Code.CANCELLED},
            )

    def disconnect(self, account_id, remove=False):
        with self.db.session(write=True) as session:
            row = require_account(session, account_id)
            assert_transition(row.status, "disconnected")
            self._cancel(session, row)
            if row.status != "disconnected":
                self.events.append(session, row, "account.disconnected", str(uuid4()), self.now())
            row.status, row.credential_configured, row.error_code, row.next_sync_at = (
                "disconnected",
                False,
                None,
                None,
            )
            session.execute(delete(Secret).where(Secret.reference == row.credential_ref))
            if remove:
                row.deleted_at = self.now()
                session.execute(delete(AccountData).where(AccountData.connection_id == row.id))
            return AccountOut.model_validate(row)

    def configure(self, account_id, config: MockConfig):
        with self.db.session(write=True) as session:
            row = require_account(session, account_id)
            if row.connector_id != "mock":
                raise AppError(Code.FORBIDDEN)
            self._cancel(session, row)
            old = MockConfig.model_validate(row.mock)
            fields = ("balance", "currency", "usage_units", "resource_status")
            if any(getattr(old, name) != getattr(config, name) for name in fields):
                row.revision += 1
            row.mock = config.model_dump(by_alias=True)
            return AccountOut.model_validate(row)

    def settings(self):
        with self.db.session() as session:
            return preferences(session)

    def update_settings(self, value: AppSettings):
        with self.db.session(write=True) as session:
            session.get(Preferences, 1).value = value.model_dump(by_alias=True)
            for row in session.scalars(
                select(Account).where(
                    Account.deleted_at.is_(None), Account.status.in_(("connected", "error"))
                )
            ):
                row.next_sync_at = plus_seconds(self.now(), value.sync_interval_seconds)
            return value

    def enqueue(self, account_id, trigger="manual"):
        with self.db.session(write=True) as session:
            return TaskOut.model_validate(self.enqueue_in(session, account_id, trigger))

    def enqueue_in(self, session, account_id, trigger="manual"):
        account = require_account(session, account_id)
        if not account.credential_configured or account.status not in {"connected", "error"}:
            raise AppError(Code.INVALID_STATE)
        if active_task(session, account.id):
            raise AppError(Code.BUSY)
        row = SyncTask(
            id=str(uuid4()),
            connection_id=account.id,
            status="pending",
            trigger=trigger,
            attempts=0,
            retry_count=0,
            max_attempts=3,
            created_at=self.now(),
            started_at=None,
            finished_at=None,
            next_attempt_at=self.now(),
            error_code=None,
            worker_owner=None,
            worker_fence=None,
        )
        session.add(row)
        session.flush()
        return row

    def cancel(self, account_id):
        with self.db.session(write=True) as session:
            self._cancel(session, require_account(session, account_id))
