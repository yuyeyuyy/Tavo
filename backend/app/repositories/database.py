from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError, Code


# 升级数据库函数，接受一个路径参数，创建必要的目录和文件权限，配置Alembic并执行数据库升级到最新版本。
def upgrade_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    path.chmod(0o600)


"""
数据库连接类，用于管理与SQLite数据库的连接和会话。
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        # 创建连接引擎。
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 5},
            hide_parameters=True,
        )

        # 配置SQLite连接的PRAGMA设置，启用外键约束、设置忙等待超时和使用WAL日志模式。 初始化设置
        @event.listens_for(self.engine, "connect")
        def configure(connection, _record):
            # 启用外键约束
            connection.execute("PRAGMA foreign_keys=ON")
            # 设置忙等待超时为5000毫秒，如果数据库被锁定，SQLite将等待最多5秒钟以获取锁。
            connection.execute("PRAGMA busy_timeout=5000")
            # 设置WAL（Write-Ahead Logging）模式，这种模式允许更高的并发性和更好的性能。
            connection.execute("PRAGMA journal_mode=WAL")

        # 创建会话工厂，设置expire_on_commit=False以防止在提交后过期对象。
        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    # 使用上下文管理器创建一个数据库会话，支持读写操作。 如果write为True，则在会话开始时执行BEGIN IMMEDIATE事务，以确保写入操作的原子性。 在会话结束时，如果write为True，则提交事务；如果发生异常，则回滚事务并抛出AppError异常。 最后，关闭会话以释放资源。
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

    # 关闭数据库连接，释放资源。
    def close(self):
        self.engine.dispose()
