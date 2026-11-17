import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr
from sqlalchemy import delete, select

from app.core.config import Settings, utcnow
from app.core.errors import AppError, Code
from app.models.entities import AssistantModelTurn, ClientSession, SandboxNetworkRequest, Secret
from app.repositories.database import Database
from app.repositories.time import plus_seconds


def private_read(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as file:
            mode = os.fstat(file.fileno())
            if not stat.S_ISREG(mode.st_mode) or (os.name == "posix" and mode.st_mode & 0o077):
                raise AppError(Code.CREDENTIAL_UNAVAILABLE)
            result = file.read(65537)
            if len(result) > 65536:
                raise AppError(Code.CREDENTIAL_UNAVAILABLE)
            return result
    except (OSError, ValueError):
        raise AppError(Code.CREDENTIAL_UNAVAILABLE) from None


def private_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise AppError(Code.CREDENTIAL_UNAVAILABLE)
    fd, name = tempfile.mkstemp(prefix=".tavo-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            os.chmod(name, 0o600)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def initialize_keys(settings: Settings):
    db = Database(settings.database)
    try:
        # Share the database writer lock with rotations and credential writes.
        # Two startup processes must never replace each other's initial keys.
        with db.session(write=True) as session:
            if not settings.keyring_file.exists():
                if session.scalar(select(Secret).limit(1)):
                    raise AppError(Code.CREDENTIAL_UNAVAILABLE)
                keyring = {
                    "version": 1,
                    "keys": [{"id": uuid4().hex, "key": Fernet.generate_key().decode()}],
                }
                private_write(settings.keyring_file, json.dumps(keyring).encode())
            if not settings.client_token_file.exists():
                private_write(settings.client_token_file, secrets.token_urlsafe(48).encode())
            private_read(settings.client_token_file)
    finally:
        db.close()


class SecretStore:
    """Server-owned encryption boundary; no HTTP credential read endpoint exists."""

    def __init__(self, database: Database, settings: Settings):
        self.db = database
        self.settings = settings

    def ring(self):
        try:
            value = json.loads(private_read(self.settings.keyring_file))
            keys = value["keys"]
            if value["version"] != 1 or not 1 <= len(keys) <= 16:
                raise ValueError("invalid keyring")
            return keys[0]["id"], MultiFernet([Fernet(k["key"].encode()) for k in keys])
        except (KeyError, ValueError, TypeError, AppError):
            raise AppError(Code.CREDENTIAL_UNAVAILABLE) from None

    def available(self):
        try:
            self.ring()
            return True
        except AppError:
            return False

    def put(self, session, reference: str, value: SecretStr):
        key_id, ring = self.ring()
        secret = value.get_secret_value()
        if not secret or len(secret) > 8192:
            raise AppError(Code.INVALID_INPUT)
        ciphertext = ring.encrypt(secret.encode())
        row = session.get(Secret, reference)
        if row:
            row.ciphertext, row.key_id = ciphertext, key_id
        else:
            session.add(Secret(reference=reference, ciphertext=ciphertext, key_id=key_id))

    def rotate(self):
        with self.db.session(write=True) as session:
            # Serialize the read/publish/reencrypt sequence across processes.
            # Publish new primary + retain old keys first: interruption stays recoverable.
            self.ring()
            value = json.loads(private_read(self.settings.keyring_file))
            if len(value["keys"]) >= 16:
                raise AppError(Code.INVALID_STATE)
            value["keys"].insert(0, {"id": uuid4().hex, "key": Fernet.generate_key().decode()})
            private_write(self.settings.keyring_file, json.dumps(value).encode())
            key_id, ring = self.ring()
            rows = [
                *session.scalars(select(Secret)),
                *session.scalars(
                    select(AssistantModelTurn).where(AssistantModelTurn.ciphertext.is_not(None))
                ),
                *session.scalars(select(SandboxNetworkRequest)),
            ]
            for row in rows:
                try:
                    row.ciphertext, row.key_id = ring.rotate(row.ciphertext), key_id
                except InvalidToken:
                    raise AppError(Code.CREDENTIAL_UNAVAILABLE) from None
            for row in session.scalars(
                select(SandboxNetworkRequest).where(
                    SandboxNetworkRequest.result_ciphertext.is_not(None)
                )
            ):
                try:
                    row.result_ciphertext, row.result_key_id = (
                        ring.rotate(row.result_ciphertext),
                        key_id,
                    )
                except InvalidToken:
                    raise AppError(Code.CREDENTIAL_UNAVAILABLE) from None


class SessionService:
    def __init__(self, database: Database, settings: Settings, now=utcnow):
        self.db, self.settings, self.now = database, settings, now

    def owner_key(self) -> bytes:
        value = private_read(self.settings.client_token_file).strip()
        if not 32 <= len(value) <= 2048:
            raise AppError(Code.CREDENTIAL_UNAVAILABLE)
        return value

    def issue(self, bootstrap: str):
        key = self.owner_key()
        if not hmac.compare_digest(bootstrap.encode(), key):
            raise AppError(Code.UNAUTHENTICATED)
        token = secrets.token_urlsafe(48)
        expires = plus_seconds(self.now(), self.settings.session_seconds)
        with self.db.session(write=True) as session:
            session.execute(delete(ClientSession).where(ClientSession.expires_at <= self.now()))
            existing = list(
                session.scalars(select(ClientSession).order_by(ClientSession.expires_at))
            )
            for old in existing[:-99]:
                session.delete(old)
            session.add(
                ClientSession(
                    digest=hashlib.sha256(token.encode()).hexdigest(),
                    expires_at=expires,
                    owner_fingerprint=hashlib.sha256(key).hexdigest(),
                )
            )
        return token, expires

    def require(self, token: str | None):
        if not token or len(token) > 2048:
            raise AppError(Code.UNAUTHENTICATED)
        with self.db.session() as session:
            row = session.get(ClientSession, hashlib.sha256(token.encode()).hexdigest())
            if (
                not row
                or row.expires_at <= self.now()
                or not hmac.compare_digest(
                    row.owner_fingerprint, hashlib.sha256(self.owner_key()).hexdigest()
                )
            ):
                raise AppError(Code.UNAUTHENTICATED)
