from sqlalchemy import func, select

from app.agent.graph import create_graph
from app.api.schemas import (
    AppSettings,
    ConnectorOut,
    DataOut,
    LegacyAccount,
    LegacyEvent,
    LegacyTask,
    SecurityOut,
    Snapshot,
    SystemStatus,
    TaskOut,
    TaskPage,
)
from app.connectors.registry import MockConnector, Registry
from app.core.config import utcnow
from app.core.errors import AppError, Code
from app.core.security import SecretStore, SessionService
from app.models.entities import (
    AccountData,
    Connector,
    LegacyRecord,
    Preferences,
    SyncTask,
    WorkerLease,
)
from app.repositories.database import Database
from app.repositories.workspace import EventRepository
from app.services.accounts import AccountService
from app.services.agent import AgentService
from app.services.events import EventService


class Services:
    def __init__(self, settings, now=utcnow, connector=None):
        self.settings, self.now = settings, now
        self.db = Database(settings.database)
        self.registry = Registry()
        self.connector = connector or MockConnector()
        self.event_repository = EventRepository()
        self.secrets = SecretStore(self.db, settings)
        self.sessions = SessionService(self.db, settings, now)
        self.accounts = AccountService(
            self.db, self.registry, self.secrets, self.event_repository, now
        )
        self.events = EventService(self.db, self.event_repository)
        self.agent = AgentService(self.db, now)
        self.graph = create_graph()

    async def initialize(self):
        await self.registry.register(self.connector)
        with self.db.session(write=True) as session:
            for value in self.registry.list():
                row = session.get(Connector, value.id)
                if row:
                    row.name, row.capabilities, row.permissions = (
                        value.name,
                        value.capabilities,
                        value.permissions,
                    )
                else:
                    session.add(
                        Connector(
                            id=value.id,
                            name=value.name,
                            capabilities=value.capabilities,
                            permissions=value.permissions,
                        )
                    )
            if not session.get(Preferences, 1):
                session.add(Preferences(id=1, value=AppSettings().model_dump(by_alias=True)))
            if not session.get(WorkerLease, 1):
                session.add(
                    WorkerLease(id=1, owner="", fence=0, expires_at="1970-01-01T00:00:00.000Z")
                )

    def security(self):
        return SecurityOut(
            available=self.secrets.available(),
            backend="Fernet keyring (server)",
            data_location=str(self.settings.data_dir),
        )

    def status(self):
        with self.db.session() as session:
            lease = session.get(WorkerLease, 1)
            return SystemStatus(
                security=self.security(),
                worker_active=bool(lease and lease.owner and lease.expires_at > self.now()),
            )

    def task(self, task_id):
        with self.db.session() as session:
            row = session.get(SyncTask, task_id)
            if not row:
                raise AppError(Code.NOT_FOUND)
            return TaskOut.model_validate(row)

    def tasks(self, limit=50, offset=0, account_id=None, status=None):
        with self.db.session() as session:
            query = select(SyncTask)
            if account_id:
                query = query.where(SyncTask.connection_id == account_id)
            if status:
                query = query.where(SyncTask.status == status)
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(
                query.order_by(SyncTask.created_at.desc(), SyncTask.id).limit(limit).offset(offset)
            )
            return TaskPage(
                items=[TaskOut.model_validate(row) for row in rows],
                total=total,
                limit=limit,
                offset=offset,
            )

    def snapshot(self):
        with self.db.session() as session:
            legacy = list(session.scalars(select(LegacyRecord)))
            data = list(session.scalars(select(AccountData)))
            connectors = [
                ConnectorOut.model_validate(row) for row in session.scalars(select(Connector))
            ]
        return Snapshot(
            connectors=connectors,
            accounts=[
                LegacyAccount.model_validate(r.data) for r in legacy if r.table_name == "accounts"
            ],
            events=[LegacyEvent.model_validate(r.data) for r in legacy if r.table_name == "events"][
                -100:
            ],
            tasks=[
                LegacyTask.model_validate(r.data) for r in legacy if r.table_name == "sync_tasks"
            ][-50:],
            connections=self.accounts.list(limit=200).items,
            data=[DataOut.model_validate(row) for row in data],
            jobs=self.tasks(limit=200).items,
            business_events=self.events.list(limit=300).items,
            settings=self.accounts.settings(),
            security=self.security(),
        )

    async def close(self):
        await self.registry.close()
        self.db.close()
