import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    keyring_file: Path
    client_token_file: Path
    origins: tuple[str, ...] = ("tavo://app", "http://localhost:5173", "http://127.0.0.1:5173")
    hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]", "testserver")
    worker_enabled: bool = True
    session_seconds: int = 900
    timeout_seconds: float = 5
    concurrency: int = 2
    lease_seconds: float = 30
    poll_seconds: float = 0.25

    @property
    def database(self) -> Path:
        return self.data_dir / "tavo.sqlite"

    @classmethod
    def from_env(cls):
        directory = Path(os.environ.get("TAVO_DATA_DIR", ".local")).resolve()
        return cls(
            data_dir=directory,
            keyring_file=Path(os.environ.get("TAVO_KEYRING_FILE", str(directory / "master.keys"))),
            client_token_file=Path(
                os.environ.get("TAVO_CLIENT_TOKEN_FILE", str(directory / "client.token"))
            ),
            origins=tuple(
                filter(None, os.environ.get("TAVO_CORS_ORIGINS", ",".join(cls.origins)).split(","))
            ),
            hosts=tuple(
                filter(None, os.environ.get("TAVO_ALLOWED_HOSTS", ",".join(cls.hosts)).split(","))
            ),
            worker_enabled=os.environ.get("TAVO_WORKER", "1") == "1",
        )
