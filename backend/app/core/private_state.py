import json

from cryptography.fernet import InvalidToken

from app.core.errors import AppError, Code


class PrivateStateStore:
    """Bounded authenticated provider continuation, never a public DTO or a tool capability."""

    def __init__(self, secrets):
        self.secrets = secrets

    def seal(self, binding, value, *, max_bytes=262144):
        data = json.dumps(
            {"binding": binding, "value": value}, ensure_ascii=False, separators=(",", ":")
        ).encode()
        if len(data) > max_bytes:
            raise AppError(Code.CONTEXT_TOO_LARGE)
        key_id, ring = self.secrets.ring()
        return key_id, ring.encrypt(data)

    def open(self, binding, ciphertext):
        try:
            _, ring = self.secrets.ring()
            value = json.loads(ring.decrypt(ciphertext))
            if value["binding"] != binding:
                raise ValueError("binding mismatch")
            return value["value"]
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise AppError(Code.CREDENTIAL_UNAVAILABLE) from None
