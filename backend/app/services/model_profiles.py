import uuid

from pydantic import ValidationError

from app.core.config import utcnow
from app.core.errors import AppError, Code
from app.llm.config import PROVIDER_NAMES, ModelProfile
from app.llm.contracts import ModelProfileList, ModelProfileOut, ProviderList, ProviderOption
from app.llm.keys import ModelKeys
from app.llm.repository import ModelProfileRepository


class ModelProfileService:
    def __init__(self, database, secrets, legacy_file, now=utcnow):
        self.repository = ModelProfileRepository(database, now)
        self.keys, self.legacy_file = ModelKeys(secrets), legacy_file

    def initialize(self):
        self.repository.initialize(self.legacy_file)

    def snapshot(self):
        return self.repository.snapshot()

    def profile(self, identity, *, enabled=True):
        return self.snapshot().profile(identity, enabled=enabled)

    def providers(self):
        return ProviderList(
            items=[ProviderOption(id=key, label=value) for key, value in PROVIDER_NAMES.items()]
        )

    def public(self, profile):
        return ModelProfileOut(
            id=profile.id,
            provider=profile.provider,
            model=profile.model,
            label=profile.label,
            enabled=profile.enabled,
            ready=self.keys.ready(profile) if profile.enabled else False,
            credential_revision=self.keys.revision(profile),
        )

    def list(self):
        config = self.snapshot()
        return ModelProfileList(
            items=[self.public(profile) for profile in config.profiles],
            default_profile_id=config.default_profile_id,
        )

    def create(self, value, before_write):
        try:
            profile = ModelProfile(
                id="model-" + uuid.uuid4().hex,
                provider=value.provider,
                model=value.model,
                label=value.label or PROVIDER_NAMES[value.provider],
                enabled=True,
            )
        except ValidationError:
            raise AppError(Code.INVALID_INPUT) from None
        self.repository.create(profile, before_write)
        return self.public(profile)
