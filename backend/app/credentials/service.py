import asyncio
import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.core.config import utcnow
from app.core.errors import AppError, Code
from app.credentials.contracts import ChallengeOut, ProtectedHeader, SubmissionAck
from app.credentials.envelope import decrypt, generate_key, public_jwk
from app.credentials.handlers import CredentialScope
from app.repositories.time import plus_seconds


@dataclass(frozen=True)
class Challenge:
    header: ProtectedHeader
    expires: str
    owner: str
    scope: CredentialScope


class SecretSubmissionService:
    """Single API-process challenge registry. Secrets never enter a business event or DTO."""

    def __init__(self, sessions, handlers, now=utcnow):
        self.sessions, self.handlers, self.now = sessions, dict(handlers), now
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tavo-secret-transfer")
        self.private = None
        self.key_future = None
        self.kid = secrets.token_urlsafe(24)
        self.challenges = {}
        self.active = 0
        self.lock = asyncio.Lock()
        self.closed = False

    def purge(self):
        now = self.now()
        self.challenges = {k: v for k, v in self.challenges.items() if v.expires > now}

    def owner(self, token):
        self.sessions.require(token)
        return hashlib.sha256(token.encode()).hexdigest()

    def handler(self, purpose):
        handler = self.handlers.get(purpose)
        if handler is None:
            raise AppError(Code.FORBIDDEN)
        return handler

    async def issue(self, value, token):
        owner = self.owner(token)
        async with self.lock:
            if self.closed:
                raise AppError(Code.SECRET_ENCRYPTION_UNAVAILABLE)
            self.purge()
            if (
                len(self.challenges) >= 64
                or sum(c.owner == owner for c in self.challenges.values()) >= 4
            ):
                raise AppError(Code.BUSY)
            scope = self.handler(value.purpose).snapshot(value.resource_id)
            if value.expected_revision != scope.revision:
                raise AppError(Code.SECRET_REVISION_CONFLICT)
            if self.private is None:
                if self.key_future is None:
                    self.key_future = self.pool.submit(generate_key)
                try:
                    self.private = await asyncio.shield(asyncio.wrap_future(self.key_future))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise AppError(Code.SECRET_ENCRYPTION_UNAVAILABLE) from None
            # Key generation may outlive a session or a CLI update.
            self.owner(token)
            if self.closed:
                raise AppError(Code.SECRET_ENCRYPTION_UNAVAILABLE)
            current = self.handler(value.purpose).snapshot(value.resource_id)
            if current != scope:
                raise AppError(Code.SECRET_REVISION_CONFLICT)
            identity = secrets.token_urlsafe(24)
            header = ProtectedHeader(
                kid=self.kid,
                challenge_id=identity,
                purpose=value.purpose,
                resource_id=value.resource_id,
            )
            expires = plus_seconds(self.now(), 120)
            self.challenges[identity] = Challenge(header, expires, owner, scope)
            return ChallengeOut(
                challenge_id=identity,
                expires_at=expires,
                kid=self.kid,
                purpose=value.purpose,
                resource_id=value.resource_id,
                credential_revision=scope.revision,
                public_jwk=public_jwk(self.private),
            )

    async def submit(self, value, token, disconnected):
        owner = self.owner(token)
        async with self.lock:
            self.purge()
            challenge = self.challenges.get(value.challenge_id)
            if self.closed or not challenge or challenge.owner != owner:
                raise AppError(Code.SECRET_CHALLENGE_INVALID)
            if self.active >= 2:
                raise AppError(Code.BUSY)
            self.challenges.pop(value.challenge_id)
            self.active += 1
        future = None
        try:
            future = asyncio.wrap_future(
                self.pool.submit(decrypt, self.private, value.envelope, challenge.header)
            )
            try:
                secret = await asyncio.shield(future)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise AppError(Code.SECRET_SUBMISSION_INVALID) from None
            self.owner(token)
            if self.closed or self.now() >= challenge.expires:
                raise AppError(Code.SECRET_CHALLENGE_INVALID)
            if await disconnected():
                raise AppError(Code.CANCELLED)

            def authorize_write():
                self.owner(token)
                if self.closed or self.now() >= challenge.expires:
                    raise AppError(Code.SECRET_CHALLENGE_INVALID)

            revision = self.handler(challenge.header.purpose).save(
                challenge.header.resource_id, secret, challenge.scope, authorize_write
            )
            return SubmissionAck(
                resource_id=challenge.header.resource_id, credential_revision=revision
            )
        finally:
            try:
                if future is not None:
                    # Cancellation cannot kill RSA/AES work. Retain the bounded slot until it ends.
                    await asyncio.shield(asyncio.gather(future, return_exceptions=True))
            finally:
                self.active -= 1

    async def close(self):
        self.closed = True
        self.challenges.clear()
        await asyncio.to_thread(self.pool.shutdown, wait=True)
        self.private = self.key_future = None
