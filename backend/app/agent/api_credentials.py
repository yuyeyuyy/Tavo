import hashlib
import json

from app.core.errors import AppError, Code
from app.credentials.handlers import CredentialScope
from app.llm.keys import ModelKeys


class ApiKeys(ModelKeys):
    def __init__(self, store, policy):
        super().__init__(store)
        self.policy = policy

    def target(self, resource_id):
        target = next(
            (t for t in self.policy.network.targets if t.id == resource_id and t.credential), None
        )
        if not target:
            raise AppError(Code.NETWORK_DENIED)
        return target

    def reference(self, target):
        binding = hashlib.sha256(
            json.dumps(target.model_dump(), sort_keys=True).encode()
        ).hexdigest()
        return f"sandbox-api:{target.id}:{binding}"


class ApiKeyHandler:
    def __init__(self, keys):
        self.keys = keys

    def snapshot(self, resource_id):
        target = self.keys.target(resource_id)
        return CredentialScope(self.keys.revision(target), self.keys.reference(target))

    def save(self, resource_id, secret, scope, before_write):
        target = self.keys.target(resource_id)
        if self.keys.reference(target) != scope.binding:
            raise AppError(Code.SECRET_CHALLENGE_INVALID)
        return self.keys.put(
            target, secret, expected_revision=scope.revision, before_write=before_write
        )
