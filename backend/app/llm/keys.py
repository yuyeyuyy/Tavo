import hashlib

from cryptography.fernet import InvalidToken
from pydantic import SecretStr
from sqlalchemy import delete, update

from app.core.errors import AppError, Code
from app.credentials.repository import CredentialVersions
from app.models.entities import Secret


class ModelKeys:
    """Only trusted, validated server profiles can select a model credential."""

    def __init__(self, store):
        self.store = store
        self.versions = CredentialVersions()

    def reference(self, profile):
        # Changing a provider/endpoint requires re-authorizing the key, even with the same ID.
        digest = hashlib.sha256(f"{profile.provider}\n{profile.endpoint}".encode()).hexdigest()
        return f"llm:{profile.id}:{digest}"

    def put(self, profile, value: SecretStr, expected_revision=None, before_write=None):
        key = value.get_secret_value()
        if not key.strip() or key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise AppError(Code.INVALID_INPUT)
        with self.store.db.session(write=True) as session:
            if before_write is not None:
                before_write()
            revision = self.versions.advance(
                session, self.reference(profile), True, expected_revision
            )
            self.store.put(session, self.reference(profile), value)
            return revision

    def remove(self, profile):
        with self.store.db.session(write=True) as session:
            self.versions.advance(session, self.reference(profile), False)
            session.execute(delete(Secret).where(Secret.reference == self.reference(profile)))

    def revision(self, profile):
        with self.store.db.session() as session:
            return self.versions.revision(session, self.reference(profile))

    def get(self, profile):
        with self.store.db.session(write=True) as session:
            row = session.get(Secret, self.reference(profile))
            if not row:
                raise AppError(Code.MODEL_NOT_CONFIGURED)
            key_id, ring = self.store.ring()
            try:
                value = ring.decrypt(row.ciphertext).decode()
            except (InvalidToken, UnicodeDecodeError):
                raise AppError(Code.CREDENTIAL_UNAVAILABLE) from None
            if row.key_id != key_id:
                session.execute(
                    update(Secret)
                    .where(Secret.reference == row.reference, Secret.ciphertext == row.ciphertext)
                    .values(ciphertext=ring.rotate(row.ciphertext), key_id=key_id)
                )
            return SecretStr(value)

    def ready(self, profile):
        try:
            self.get(profile)
            return True
        except AppError:
            return False
