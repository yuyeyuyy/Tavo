from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import SecretStr

from app.core.errors import AppError, Code


@dataclass(frozen=True)
class CredentialScope:
    revision: int
    binding: str = field(repr=False)


class CredentialHandler(Protocol):
    def snapshot(self, resource_id: str) -> CredentialScope: ...
    def save(
        self,
        resource_id: str,
        secret: SecretStr,
        scope: CredentialScope,
        before_write: Callable[[], None],
    ) -> int: ...


class ModelKeyHandler:
    def __init__(self, chat):
        self.chat = chat

    def snapshot(self, resource_id):
        profile = self.chat.config.profile(resource_id)
        return CredentialScope(self.chat.keys.revision(profile), self.chat.keys.reference(profile))

    def save(self, resource_id, secret, scope, before_write):
        profile = self.chat.config.profile(resource_id)
        if self.chat.keys.reference(profile) != scope.binding:
            raise AppError(Code.SECRET_CHALLENGE_INVALID)
        return self.chat.keys.put(
            profile, secret, expected_revision=scope.revision, before_write=before_write
        )
