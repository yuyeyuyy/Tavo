from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.errors import AppError, Code
from app.llm.config import ModelConfig, ModelProfile, load_models
from app.models.entities import ModelCatalogState, ModelProfileRecord


class ModelProfileRepository:
    def __init__(self, database, now):
        self.db, self.now = database, now

    def initialize(self, legacy_file):
        # The marker and all imported rows commit together, including an empty import.
        with self.db.session(write=True) as session:
            if session.get(ModelCatalogState, 1):
                return
            config = load_models(legacy_file)
            for index, profile in enumerate(config.profiles):
                self.insert(session, profile, index)
            session.flush()
            session.add(ModelCatalogState(id=1, default_profile_id=config.default_profile_id))

    def insert(self, session, profile, position):
        session.add(
            ModelProfileRecord(**profile.model_dump(), position=position, created_at=self.now())
        )

    def snapshot(self):
        with self.db.session() as session:
            state = session.get(ModelCatalogState, 1)
            if not state:
                raise AppError(Code.MODEL_CONFIG_INVALID)
            rows = session.scalars(
                select(ModelProfileRecord).order_by(
                    ModelProfileRecord.position, ModelProfileRecord.id
                )
            )
            try:
                return ModelConfig(
                    profiles=[ModelProfile.model_validate(row) for row in rows],
                    default_profile_id=state.default_profile_id,
                )
            except ValidationError:
                raise AppError(Code.MODEL_CONFIG_INVALID) from None

    def create(self, profile, before_write):
        with self.db.session(write=True) as session:
            before_write()
            count = session.scalar(select(func.count()).select_from(ModelProfileRecord))
            if count >= 32:
                raise AppError(Code.MODEL_PROFILE_LIMIT)
            self.insert(session, profile, count)
