from app.agent.api_credentials import ApiKeyHandler
from app.agent.assistant import Assistant
from app.agent.bash import BashRunner
from app.agent.host import HostBashRunner
from app.agent.sandbox import SandboxManager
from app.api.schemas import SecurityOut, SystemStatus
from app.core.config import utcnow
from app.core.security import SecretStore, SessionService
from app.credentials.handlers import ModelKeyHandler
from app.credentials.service import SecretSubmissionService
from app.models.entities import WorkerLease
from app.repositories.database import Database
from app.repositories.sessions import SessionRepository
from app.services.chat import ChatService
from app.services.model_profiles import ModelProfileService
from app.services.sessions import ConversationService


class Services:
    """Application composition. Authentication sessions are distinct from conversations."""

    def __init__(self, settings, now=utcnow):
        self.settings, self.now = settings, now
        self.db = Database(settings.database)
        self.secrets = SecretStore(self.db, settings)
        self.sessions = SessionService(self.db, settings, now)
        self.models = ModelProfileService(self.db, self.secrets, settings.models_file, now)
        self.chat = ChatService(self.secrets, self.models, retry_policy=settings.model_retry)
        self.conversations = SessionRepository(self.db, now)
        self.bash = BashRunner(settings, self.db, now)
        self.bash.manager = SandboxManager(self.bash, self.secrets)
        self.host = HostBashRunner(settings, self.db, self.secrets, now)
        self.session_service = ConversationService(self.conversations, self.sessions, self.models)
        self.assistant = Assistant(
            self.db,
            self.conversations,
            self.secrets,
            self.models,
            self.chat.gateway,
            now,
            bash=self.bash,
            host=self.host,
        )
        self.secret_submissions = SecretSubmissionService(
            self.sessions,
            {
                "model.api_key": ModelKeyHandler(self.chat),
                "sandbox.api_key": ApiKeyHandler(self.bash.manager.egress.keys),
            },
            now,
        )

    async def initialize(self):
        await self.bash.prepare()
        self.models.initialize()
        with self.db.session(write=True) as db:
            if not db.get(WorkerLease, 1):
                db.add(WorkerLease(id=1, owner="", fence=0, expires_at="1970-01-01T00:00:00.000Z"))

    def security(self):
        return SecurityOut(
            available=self.secrets.available(),
            backend="Fernet keyring (server)",
            data_location=str(self.settings.data_dir),
        )

    def status(self):
        with self.db.session() as db:
            lease = db.get(WorkerLease, 1)
            return SystemStatus(
                security=self.security(),
                worker_active=bool(lease and lease.expires_at > self.now()),
            )

    async def close(self):
        await self.secret_submissions.close()
        await self.assistant.close()
        await self.host.close()
        await self.chat.close()
        self.db.close()
