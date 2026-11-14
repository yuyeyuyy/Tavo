from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError, Code


def upgrade_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    path.chmod(0o600)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 5},
            hide_parameters=True,
        )

        @event.listens_for(self.engine, "connect")
        def configure(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")

        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self, write: bool = False) -> Iterator[Session]:
        session = self.factory()
        try:
            if write:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield session
            if write:
                session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise AppError(Code.STORAGE_FAILED) from None
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self):
        self.engine.dispose()
