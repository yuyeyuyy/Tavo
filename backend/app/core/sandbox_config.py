"""Administrator-owned policy. Model arguments can select profiles, never change ceilings."""

import hashlib
import ipaddress
import json
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "sandbox" / "defaults.toml"


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class NetworkTarget(PolicyModel):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    hostname: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]$")]
    paths: Annotated[list[str], Field(min_length=1, max_length=32)]
    methods: list[Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]] = ["GET", "HEAD"]
    capability: Literal["public_read", "api_test", "package_download"] = "public_read"
    credential: bool = False
    auth_header: Literal["Authorization", "x-api-key"] = "Authorization"
    auth_prefix: Literal["Bearer ", ""] = "Bearer "

    @model_validator(mode="after")
    def bounded(self):
        try:
            ipaddress.ip_address(self.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("Domain name required, never a literal IP")
        if any(not p.startswith("/") or ".." in p or "?" in p or "#" in p for p in self.paths):
            raise ValueError("Normalized absolute path prefixes required")
        if self.capability != "api_test" and (
            self.credential or any(m not in {"GET", "HEAD"} for m in self.methods)
        ):
            raise ValueError("Read-only capability required")
        return self


class PackagePin(PolicyModel):
    name: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")]
    version: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.+!-]{0,99}$")]
    url: Annotated[str, Field(max_length=2048)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def source(self):
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.endswith(".whl")
        ):
            raise ValueError("Exact HTTPS wheel source required")
        return self


class NetworkPolicy(PolicyModel):
    mode: Literal["none", "proxy"] = "proxy"
    allow_public_https: bool = True
    require_write_approval: bool = False
    dns_mode: Literal["system", "cloudflare-doh"] = "cloudflare-doh"
    request_seconds: Annotated[int, Field(ge=1, le=120)] = 30
    response_bytes: Annotated[int, Field(ge=1024, le=2097152)] = 2097152
    request_bytes: Annotated[int, Field(ge=1024, le=65536)] = 65536
    requests_per_minute: Annotated[int, Field(ge=1, le=120)] = 30
    requests_per_run: Annotated[int, Field(ge=1, le=1000)] = 100
    redirects: Annotated[int, Field(ge=0, le=5)] = 3
    download_bytes: Annotated[int, Field(ge=1024, le=268435456)] = 67108864
    install_bytes: Annotated[int, Field(ge=1048576, le=536870912)] = 268435456
    install_files: Annotated[int, Field(ge=1, le=20000)] = 10000
    protected_cidrs: list[str] = []
    targets: Annotated[list[NetworkTarget], Field(max_length=32)] = []
    packages: Annotated[list[PackagePin], Field(max_length=100)] = []

    @model_validator(mode="after")
    def unique(self):
        for cidr in self.protected_cidrs:
            ipaddress.ip_network(cidr)
        if len({t.id for t in self.targets}) != len(self.targets):
            raise ValueError("Duplicate network target")
        return self


class HostPolicy(PolicyModel):
    enabled: bool = True
    default_cwd: str = "."
    command_max_bytes: Annotated[int, Field(ge=1, le=32768)] = 32768
    default_timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 60
    max_timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 300
    capture_bytes: Annotated[int, Field(ge=4096, le=1048576)] = 65536
    public_output_bytes: Annotated[int, Field(ge=256, le=2048)] = 2048
    max_processes: Literal[1] = 1
    terminate_grace_seconds: Annotated[float, Field(gt=0, le=5)] = 0.5
    heartbeat_seconds: Annotated[float, Field(gt=0, le=5)] = 1.0
    watchdog_seconds: Annotated[float, Field(ge=0.5, le=15)] = 10.0

    @model_validator(mode="after")
    def limits(self):
        if (
            self.default_timeout_seconds > self.max_timeout_seconds
            or self.heartbeat_seconds >= self.watchdog_seconds
        ):
            raise ValueError("Invalid host limits")
        return self


class SandboxPolicy(PolicyModel):
    schema_version: Literal[3] = 3
    image: str
    command_max_bytes: Annotated[int, Field(ge=1, le=32768)]
    argument_max_bytes: Annotated[int, Field(ge=32768, le=1048576)]
    default_timeout_seconds: Annotated[int, Field(ge=1, le=300)]
    max_timeout_seconds: Annotated[int, Field(ge=1, le=300)]
    cpu: Annotated[float, Field(gt=0, le=2)]
    max_cpu: Annotated[float, Field(gt=0, le=2)]
    memory_mib: Annotated[int, Field(ge=128, le=1024)]
    max_memory_mib: Annotated[int, Field(ge=128, le=1024)]
    pids_limit: Annotated[int, Field(ge=16, le=128)]
    max_file_bytes: Annotated[int, Field(ge=1024, le=67108864)]
    max_sandboxes: Literal[1]
    capture_bytes: Annotated[int, Field(ge=4096, le=1048576)]
    public_output_bytes: Annotated[int, Field(ge=256, le=2048)]
    temporary_mib: Annotated[int, Field(ge=16, le=512)]
    workspace_max_bytes: Annotated[int, Field(ge=1048576, le=2147483648)]
    workspace_max_files: Annotated[int, Field(ge=1, le=20000)]
    retained_versions: Annotated[int, Field(ge=1, le=10)]
    delete_approval_files: Annotated[int, Field(ge=1, le=100)]
    overwrite_approval_files: Annotated[int, Field(ge=1, le=1000)]
    idle_seconds: Annotated[int, Field(ge=1, le=300)]
    queue_timeout_seconds: Annotated[int, Field(ge=1, le=600)]
    build_timeout_seconds: Annotated[int, Field(ge=1, le=600)]
    warmup_timeout_seconds: Annotated[int, Field(ge=1, le=30)]
    max_rounds: Annotated[int, Field(ge=4, le=32)]
    max_tool_calls: Annotated[int, Field(ge=8, le=64)]
    max_active_seconds: Annotated[int, Field(ge=300, le=3600)]
    context_bytes: Annotated[int, Field(ge=65536, le=1048576)]
    file_chunk_bytes: Annotated[int, Field(ge=1024, le=32768)]
    identical_failures: Annotated[int, Field(ge=1, le=3)]
    network: NetworkPolicy
    host: HostPolicy = Field(default_factory=HostPolicy)

    @model_validator(mode="after")
    def limits(self):
        if (
            self.cpu > self.max_cpu
            or self.memory_mib > self.max_memory_mib
            or self.default_timeout_seconds > self.max_timeout_seconds
            or self.max_active_seconds <= self.max_timeout_seconds
        ):
            raise ValueError("Invalid resource ceilings")
        return self

    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=1)
def default_policy():
    return SandboxPolicy.model_validate(tomllib.loads(DEFAULT_POLICY.read_text()))


def _load_policy(path: Path | None = None):
    value = default_policy().model_dump()
    if path:
        supplied = tomllib.loads(path.read_text())
        value["host"] = {**value["host"], **supplied.pop("host", {})}
        incoming = supplied.pop("network", {})
        network = {**value["network"], **incoming}
        network["protected_cidrs"] = list(
            dict.fromkeys(value["network"]["protected_cidrs"] + incoming.get("protected_cidrs", []))
        )
        value.update(supplied)
        value["network"] = network
    return SandboxPolicy.model_validate(value)


def load_policy(path: Path | None = None):
    from pydantic import ValidationError

    from app.core.errors import AppError, Code

    try:
        return _load_policy(path)
    except (OSError, ValueError, TypeError, ValidationError):
        raise AppError(Code.SANDBOX_CONFIG_INVALID) from None
