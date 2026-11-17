import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.core.sandbox_config import SandboxPolicy, default_policy, load_policy
from app.llm.retry import ModelRetryPolicy


# 获取当前 UTC 时间
def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


"""
应用配置类
"""


@dataclass(frozen=True)
class Settings:
    # 数据库文件路径
    data_dir: Path
    # 密钥环文件路径
    keyring_file: Path
    # 客户端令牌文件路径
    client_token_file: Path
    # CORS 允许的来源和主机
    origins: tuple[str, ...] = ("tavo://app", "http://localhost:5173", "http://127.0.0.1:5173")
    # 允许的主机名
    hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]", "testserver")
    # 是否启用后台任务工作线程
    worker_enabled: bool = True
    # 会话超时时间（秒）
    session_seconds: int = 900
    # 并发数、租约时间和轮询间隔
    concurrency: int = 2
    lease_seconds: float = 30
    poll_seconds: float = 0.25
    bash_enabled: bool = True
    bash_image: str = default_policy().image
    sandbox: SandboxPolicy = field(default_factory=default_policy)
    docker_executable: str = "docker"
    host_bash_enabled: bool = True
    llm_config_file: Path | None = None
    model_retry: ModelRetryPolicy = field(default_factory=ModelRetryPolicy)

    @property
    def models_file(self) -> Path:
        return self.llm_config_file or self.data_dir / "models.json"

    # 数据库文件路径
    @property
    def database(self) -> Path:
        return self.data_dir / "tavo.sqlite"

    # 读取环境变量。进行配置
    @classmethod
    def from_env(cls):
        # 获取数据目录路径，默认为 .local
        directory = Path(os.environ.get("TAVO_DATA_DIR", ".local")).resolve()
        policy = load_policy(
            Path(os.environ["TAVO_SANDBOX_CONFIG"])
            if os.environ.get("TAVO_SANDBOX_CONFIG")
            else None
        )
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
            model_retry=ModelRetryPolicy.from_env(),
            bash_enabled=os.environ.get("TAVO_BASH_ENABLED", "1") == "1",
            host_bash_enabled=os.environ.get("TAVO_HOST_BASH_ENABLED", "1") == "1",
            bash_image=os.environ.get("TAVO_BASH_IMAGE", policy.image),
            sandbox=policy,
            worker_enabled=os.environ.get("TAVO_WORKER", "1") == "1",
            llm_config_file=Path(os.environ["TAVO_LLM_CONFIG_FILE"]).resolve()
            if os.environ.get("TAVO_LLM_CONFIG_FILE")
            else None,
        )
