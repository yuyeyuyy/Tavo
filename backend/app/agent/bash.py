import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Annotated
from uuid import uuid4

from pydantic import Field, ValidationInfo, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import select

from app.api.base import DTO
from app.core.errors import AppError, Code
from app.core.sandbox_config import default_policy
from app.core.startup import report_startup, startup_stage
from app.models.entities import AssistantReview, BashExecution, ConversationSession
from app.repositories.sessions import require_leader, require_scope

DEFAULT_BASH_IMAGE = default_policy().image
SANDBOX_DIRECTORY = Path(__file__).resolve().parents[2] / "sandbox"


def image_fingerprint():
    hashed = hashlib.sha256()
    paths = [
        SANDBOX_DIRECTORY / "Dockerfile",
        SANDBOX_DIRECTORY / "requirements.lock",
        *sorted((SANDBOX_DIRECTORY / "runtime").rglob("*.py")),
    ]
    for path in paths:
        hashed.update(str(path.relative_to(SANDBOX_DIRECTORY)).encode())
        hashed.update(path.read_bytes())
    return hashed.hexdigest()


class BashArguments(DTO):
    command: Annotated[str, Field(min_length=1, max_length=default_policy().command_max_bytes)]
    cwd: Annotated[str, Field(min_length=1, max_length=256)] = "."
    timeout_seconds: Annotated[
        int, Field(strict=True, ge=1, le=default_policy().max_timeout_seconds)
    ] = default_policy().default_timeout_seconds
    resource_profile: Annotated[str, Field(pattern="^(default|expanded)$")] = "default"

    @model_validator(mode="after")
    def bounded(self, info: ValidationInfo):
        policy = (info.context or {}).get("policy", default_policy())
        if len(self.command.encode()) > policy.command_max_bytes:
            raise PydanticCustomError("command_too_long", "COMMAND_TOO_LONG")
        if self.timeout_seconds > policy.max_timeout_seconds:
            raise PydanticCustomError("resource_limit", "RESOURCE_LIMIT")
        if not self.command.strip() or "\x00" in self.command:
            raise ValueError("Invalid script")
        path = PurePosixPath(self.cwd)
        if path.is_absolute() or ".." in path.parts or "\x00" in self.cwd or "\\" in self.cwd:
            raise ValueError("Workspace-relative directory required")
        return self


class BashResult(DTO):
    exit_code: int | None
    stdout: str
    stderr: str
    truncated: bool = False
    timed_out: bool = False
    success: bool = False
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: int = 0
    sandbox_id: str | None = None
    execution_target: str | None = None
    execution_id: str | None = None


class WorkspaceWriteArguments(DTO):
    path: Annotated[str, Field(min_length=1, max_length=256)]
    content: Annotated[str, Field(max_length=32768)]
    offset: Annotated[int, Field(strict=True, ge=0)] = 0
    expected_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None

    @model_validator(mode="after")
    def relative(self, info: ValidationInfo):
        policy = (info.context or {}).get("policy", default_policy())
        if len(self.content.encode()) > policy.file_chunk_bytes:
            raise ValueError("Chunk too large")
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\x00" in self.path
            or "\\" in self.path
            or self.path in {".", ""}
        ):
            raise ValueError("Workspace-relative file required")
        return self


class NetworkRequestArguments(DTO):
    request_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


def bash_result(
    error=None,
    *,
    exit_code=None,
    stdout="",
    stderr="",
    duration_ms=0,
    sandbox_id=None,
    truncated=False,
):
    from app.core.errors import MESSAGES

    result = BashResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        success=error is None,
        error_code=str(error) if error else None,
        error_message=MESSAGES.get(error) if error else None,
        duration_ms=duration_ms,
        sandbox_id=sandbox_id,
        timed_out=error == Code.TIMEOUT,
        truncated=truncated,
    )
    while len(json.dumps(result.model_dump(by_alias=True), ensure_ascii=False).encode()) > 7800:
        result.stdout = result.stdout[: len(result.stdout) // 2]
        result.stderr = result.stderr[: len(result.stderr) // 2]
        result.truncated = True
    return result


class BashRunner:
    """No host shell or host bind mounts. Each invocation owns a removable container."""

    def __init__(self, settings, database, now):
        self.settings, self.db, self.now = settings, database, now
        self.instance = hashlib.sha256(str(settings.database.resolve()).encode()).hexdigest()[:16]
        self.executable = (
            shutil.which(settings.docker_executable) if settings.bash_enabled else None
        )
        self.image_id = None
        self.available = False
        self.reason = "BASH_DISABLED" if not settings.bash_enabled else "BASH_UNAVAILABLE"
        self.pending = {}  # execution id -> Run id, retained when cleanup cannot be confirmed
        self.lock = asyncio.Lock()
        self.probed_at = 0.0
        self.manager = None

    async def cli(self, *args, timeout=10, limit=65536, input_data=None, truncate=False):
        if not self.executable:
            raise AppError(Code.BASH_UNAVAILABLE)
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            in {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR"}
        }
        proc = await asyncio.create_subprocess_exec(
            self.executable,
            *args,
            stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        size = 0
        buffers = [bytearray(), bytearray()]

        async def read(stream, index):
            nonlocal size
            while data := await stream.read(4096):
                size += len(data)
                if size > limit:
                    if not truncate:
                        raise AppError(Code.TOOL_OUTPUT_LIMIT)
                    buffers[index].extend(data[: max(0, limit - len(buffers[index]))])
                else:
                    buffers[index].extend(data)

        async def write():
            if input_data is not None:
                try:
                    proc.stdin.write(input_data)
                    await proc.stdin.drain()
                finally:
                    proc.stdin.close()

        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(
                    read(proc.stdout, 0), read(proc.stderr, 1), write(), proc.wait()
                )
            return proc.returncode, bytes(buffers[0]), bytes(buffers[1])
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def probe(self, force=False):
        if not self.executable:
            if self.settings.bash_enabled:
                self.reason = Code.DOCKER_NOT_INSTALLED.value
            return
        if not force and time.monotonic() - self.probed_at < 5:
            return
        self.probed_at = time.monotonic()
        self.available = False
        self.reason = Code.DOCKER_NOT_RUNNING.value
        try:
            rc, data, _ = await self.cli("info", "--format", "{{json .}}")
            if rc:
                return
            info = json.loads(data)
            supported = all(
                info.get(key) is True
                for key in ("MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsQuota")
            )
            seccomp = any(
                value.startswith("name=seccomp,") and "profile=unconfined" not in value
                for value in info.get("SecurityOptions", [])
            )
            self.reason = Code.BASH_ISOLATION_UNSUPPORTED.value
            if not supported or not seccomp:
                return
            self.reason = Code.BASH_IMAGE_MISSING.value
            rc, data, _ = await self.cli(
                "image",
                "inspect",
                "--format",
                '{{.Id}} {{.Os}} {{index .Config.Labels "tavo.sandbox.version"}} {{index .Config.Labels "tavo.sandbox.digest"}}',
                self.settings.bash_image,
            )
            if rc:
                return
            values = data.decode().strip().split()
            if (
                len(values) != 4
                or values[1] != "linux"
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", values[0])
            ):
                self.reason = (
                    Code.BASH_IMAGE_MISSING.value
                    if self.settings.bash_image == DEFAULT_BASH_IMAGE
                    else Code.BASH_ISOLATION_UNSUPPORTED.value
                )
                return
            if values[2] != "3" or (
                self.settings.bash_image == DEFAULT_BASH_IMAGE and values[3] != image_fingerprint()
            ):
                self.reason = (
                    Code.BASH_IMAGE_MISSING.value
                    if self.settings.bash_image == DEFAULT_BASH_IMAGE
                    else Code.BASH_ISOLATION_UNSUPPORTED.value
                )
                return
            self.image_id, self.available, self.reason = values[0], True, None
        except (
            AppError,
            OSError,
            TimeoutError,
            UnicodeError,
            ValueError,
            TypeError,
            AttributeError,
        ):
            self.available = False

    def container_options(self, name, execution_id, *, profile="default", policy=None):
        """One security configuration for startup validation and real tool invocations."""
        policy = policy or self.settings.sandbox
        memory = policy.max_memory_mib if profile == "expanded" else policy.memory_mib
        cpu = policy.max_cpu if profile == "expanded" else policy.cpu
        return [
            "--pull=never",
            "--name",
            name,
            "--label",
            "tavo.instance=" + self.instance,
            "--label",
            "tavo.execution=" + execution_id,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=10001:10001",
            f"--cpus={cpu}",
            f"--memory={memory}m",
            f"--memory-swap={memory}m",
            f"--pids-limit={policy.pids_limit}",
            "--ulimit",
            f"fsize={policy.max_file_bytes}:{policy.max_file_bytes}",
            "--ulimit",
            "core=0:0",
            "--log-driver=none",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={policy.temporary_mib}m,mode=1777",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp",
            "--env",
            "LANG=C.UTF-8",
        ]

    async def prepare(self):
        """Strict startup: validate daemon, prepare the trusted default image, execute a warmup."""
        if not self.settings.bash_enabled:
            report_startup("Bash 已由服务端配置关闭，跳过 Docker 检查与预热。")
            return
        async with startup_stage("[1/4] 正在检查 Docker 服务、隔离能力与本地镜像…"):
            await self.probe(force=True)
        if not self.available and self.reason != Code.BASH_IMAGE_MISSING.value:
            raise AppError(Code(self.reason) if self.reason in Code else Code.BASH_UNAVAILABLE)
        report_startup("[1/4] Docker 服务与隔离检查通过。")
        if (
            self.reason == Code.BASH_IMAGE_MISSING.value
            and self.settings.bash_image == DEFAULT_BASH_IMAGE
        ):
            if not (SANDBOX_DIRECTORY / "Dockerfile").is_file():
                raise AppError(Code.BASH_IMAGE_BUILD_FAILED)
            try:
                async with startup_stage(
                    f"[2/4] 默认镜像缺失或版本过期，正在构建；需要时下载基础镜像（最长 {self.settings.sandbox.build_timeout_seconds} 秒）…"
                ):
                    rc, _, _ = await self.cli(
                        "build",
                        "--tag",
                        DEFAULT_BASH_IMAGE,
                        "--file",
                        str(SANDBOX_DIRECTORY / "Dockerfile"),
                        "--label",
                        "tavo.sandbox.digest=" + image_fingerprint(),
                        str(SANDBOX_DIRECTORY),
                        timeout=self.settings.sandbox.build_timeout_seconds,
                    )
                    if rc:
                        raise AppError(Code.BASH_IMAGE_BUILD_FAILED)
                    await self.probe(force=True)
            except (OSError, TimeoutError, AppError):
                raise AppError(Code.BASH_IMAGE_BUILD_FAILED) from None
            if self.available:
                report_startup("[2/4] 执行镜像构建完成并验证通过。")
        elif self.available:
            report_startup("[2/4] 本地执行镜像已存在，直接复用，无需拉取或构建。")
        else:
            report_startup("[2/4] 配置的执行镜像不存在。")
        if not self.available:
            raise AppError(Code(self.reason) if self.reason in Code else Code.BASH_UNAVAILABLE)
        execution_id = uuid4().hex
        name = "tavo-bash-warmup-" + self.instance + "-" + execution_id
        if self.manager:
            await self.manager.claim_warmup(name, execution_id)
        try:
            async with startup_stage("[3/4] 正在创建并运行隔离预热容器…"):
                options = self.container_options(name, execution_id)
                if self.manager:
                    socket_volume = await self.manager.warmup_network(execution_id)
                    if socket_volume:
                        options.extend(
                            [
                                "--mount",
                                "type=volume,src="
                                + socket_volume
                                + ",dst=/run/tavo-egress,readonly",
                                "--env",
                                "TAVO_EGRESS_SOCKET=/run/tavo-egress/proxy.sock",
                            ]
                        )
                health = 'import os,pip,pytest,requests,httpx,pydantic,tavo_sandbox.workspace,tavo_sandbox.egress,tavo_sandbox.control; from tavo_sandbox.http import call; assert not os.environ.get("TAVO_EGRESS_SOCKET") or call("/packages",{}) == []'
                rc, output, _ = await self.cli(
                    "run",
                    "--rm",
                    *options,
                    "--label",
                    "tavo.kind=warmup",
                    self.image_id,
                    "/bin/bash",
                    "--noprofile",
                    "--norc",
                    "-c",
                    f'test "$(id -u)" = 10001 && python -I -c {shlex.quote(health)} && printf tavo-bash-ready',
                    timeout=self.settings.sandbox.warmup_timeout_seconds,
                )
                if rc or output != b"tavo-bash-ready":
                    raise AppError(Code.BASH_STARTUP_FAILED)
        except (OSError, TimeoutError, AppError):
            self.available, self.reason = False, Code.BASH_STARTUP_FAILED.value
            raise AppError(Code.BASH_STARTUP_FAILED) from None
        finally:
            async with startup_stage("[4/4] 正在确认预热容器已清理…"):
                cleanup = asyncio.create_task(self.remove(name, execution_id))
                try:
                    clean = await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    clean = await cleanup
                    if self.manager:
                        clean = await self.manager.clean_warmup_network(execution_id) and clean
                        self.manager.finish_warmup(execution_id, clean)
                    raise
            if self.manager:
                clean = await self.manager.clean_warmup_network(execution_id) and clean
                self.manager.finish_warmup(execution_id, clean)
            if not clean:
                self.available, self.reason = False, Code.BASH_STARTUP_FAILED.value
                raise AppError(Code.BASH_STARTUP_FAILED)
        report_startup("[4/4] Bash 沙箱准备完成，预热容器已清理；工具调用时将创建独立容器。")

    def busy_slots(self):
        return set(self.pending.values()) | (self.manager.busy_slots() if self.manager else set())

    def volume(self, session_id):
        return (
            "tavo-bash-"
            + self.instance
            + "-"
            + hashlib.sha256(session_id.encode()).hexdigest()[:24]
        )

    async def workspace(self, session_id):
        name = self.volume(session_id)
        rc, _, _ = await self.cli(
            "volume",
            "create",
            "--label",
            "tavo.instance=" + self.instance,
            "--label",
            "tavo.session=" + session_id,
            name,
        )
        if rc:
            raise AppError(Code.BASH_UNAVAILABLE)
        rc, raw, _ = await self.cli("volume", "inspect", "--format", "{{json .Labels}}", name)
        labels = json.loads(raw)
        if (
            rc
            or not isinstance(labels, dict)
            or labels.get("tavo.instance") != self.instance
            or labels.get("tavo.session") != session_id
        ):
            raise AppError(Code.FORBIDDEN)
        return name

    async def remove(self, name, execution_id):
        try:
            rc, raw, _ = await self.cli("inspect", "--format", "{{json .Config.Labels}}", name)
            if rc:
                # A missing container is clean only if the daemon is reachable.
                health, found, _ = await self.cli(
                    "ps", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"
                )
                return health == 0 and not found.strip()
            labels = json.loads(raw)
            if (
                labels.get("tavo.instance") != self.instance
                or labels.get("tavo.execution") != execution_id
            ):
                return False
            rc, _, _ = await self.cli("rm", "--force", name)
            return rc == 0
        except (AppError, OSError, TimeoutError, ValueError, AttributeError):
            return False

    def unknown(self, db, row):
        row.state, row.error_code = "unknown", "TOOL_EXECUTION_UNKNOWN"
        row.finished_at = self.now()
        db.get(ConversationSession, row.session_id).workspace_unconfirmed = True

    async def reconcile(self, owner, fence, *, only_owned=False):
        """Leader recovery/terminal cancellation; never replay a write command."""
        if not self.settings.bash_enabled:
            return
        if self.manager:
            await self.manager.reconcile(owner, fence, only_owned)
        with self.db.session() as db:
            rows = list(
                db.scalars(
                    select(BashExecution).where(
                        BashExecution.state.in_(("planned", "created", "running", "unknown"))
                    )
                )
            )
        for row in rows:
            if only_owned and (row.worker_owner != owner or row.worker_fence != fence):
                continue
            with self.db.session() as db:
                if not only_owned:
                    require_leader(db, owner, fence, self.now())
                from app.models.entities import AssistantRun, AssistantToolCall

                call = db.get(AssistantToolCall, row.tool_call_id)
                run = db.get(AssistantRun, call.run_id)
                obsolete = (
                    only_owned
                    or row.worker_owner != owner
                    or row.worker_fence != fence
                    or run.status != "running"
                )
            if not obsolete and row.state != "unknown":
                continue
            self.pending[row.id] = run.id
            clean = await self.remove(row.container_name, row.id)
            with self.db.session(write=True) as db:
                record = db.get(BashExecution, row.id)
                self.unknown(db, record)
                if clean:
                    record.state = "reaped"
            if clean:
                self.pending.pop(row.id, None)

    async def execute(self, scope, call_id, arguments):
        async with self.lock:
            await self.probe()
            if not self.available or self.pending:
                raise AppError(Code.BASH_UNAVAILABLE)
            execution_id = uuid4().hex
            name = "tavo-bash-" + self.instance + "-" + execution_id
            with self.db.session(write=True) as db:
                require_scope(db, scope, self.now())
                session = db.get(ConversationSession, scope.session_id)
                if session.workspace_unconfirmed and not db.scalar(
                    select(AssistantReview.id).where(
                        AssistantReview.tool_call_id == call_id,
                        AssistantReview.source == "human",
                        AssistantReview.action == "allow",
                    )
                ):
                    raise AppError(Code.FORBIDDEN)
                db.add(
                    BashExecution(
                        id=execution_id,
                        tool_call_id=call_id,
                        session_id=scope.session_id,
                        container_name=name,
                        state="planned",
                        worker_owner=scope.owner,
                        worker_fence=scope.fence,
                        created_at=self.now(),
                    )
                )
            self.pending[execution_id] = scope.run_id
            started = False
            outcome = None
            try:
                volume = await self.workspace(scope.session_id)
                rc, data, _ = await self.cli(
                    "create",
                    *self.container_options(name, execution_id),
                    "--mount",
                    "type=volume,src=" + volume + ",dst=/workspace",
                    self.image_id,
                    "/bin/bash",
                    "--noprofile",
                    "--norc",
                    "-c",
                    'cd -P -- "$1" || exit 126; case "$PWD" in /workspace|/workspace/*) exec /bin/bash --noprofile --norc -c "$2";; *) printf "Invalid working directory\n" >&2; exit 126;; esac',
                    "tavo-bash",
                    arguments.cwd,
                    arguments.command,
                )
                container_id = data.decode().strip()
                if rc or not re.fullmatch(r"[a-f0-9]{64}", container_id):
                    raise AppError(Code.BASH_UNAVAILABLE)
                with self.db.session(write=True) as db:
                    require_scope(db, scope, self.now())
                    row = db.get(BashExecution, execution_id)
                    row.container_id, row.state = container_id, "created"
                    row.state = "running"  # durable intent before starting any shell
                started = True
                rc, stdout, stderr = await self.cli(
                    "start", "--attach", name, timeout=arguments.timeout_seconds
                )
                # CLI status is not the command status when the engine disconnects.
                status, raw, _ = await self.cli("inspect", "--format", "{{json .State}}", name)
                state = json.loads(raw)
                if (
                    status
                    or state.get("Status") != "exited"
                    or rc != state.get("ExitCode")
                    or state.get("Running")
                    or not state.get("FinishedAt")
                    or state.get("Error")
                ):
                    raise AppError(Code.TOOL_EXECUTION_UNKNOWN)
                code = state["ExitCode"]
                outcome = BashResult(
                    exit_code=code,
                    stdout=stdout[:2048].decode("utf-8", "replace"),
                    stderr=stderr[:2048].decode("utf-8", "replace"),
                    truncated=len(stdout) > 2048 or len(stderr) > 2048,
                )
                while (
                    len(json.dumps(outcome.model_dump(by_alias=True), ensure_ascii=False).encode())
                    > 7800
                ):
                    outcome.stdout = outcome.stdout[: len(outcome.stdout) // 2]
                    outcome.stderr = outcome.stderr[: len(outcome.stderr) // 2]
                    outcome.truncated = True
                error = None if code == 0 else "TOOL_COMMAND_FAILED"
                return outcome, error
            except TimeoutError:
                outcome = BashResult(exit_code=None, stdout="", stderr="", timed_out=True)
                return outcome, "TIMEOUT"
            except AppError as error:
                if error.code == Code.TOOL_OUTPUT_LIMIT:
                    outcome = BashResult(exit_code=None, stdout="", stderr="", truncated=True)
                    return outcome, error.code.value
                if started:
                    raise AppError(Code.TOOL_EXECUTION_UNKNOWN) from None
                raise
            except (OSError, ValueError, UnicodeError):
                raise AppError(
                    Code.TOOL_EXECUTION_UNKNOWN if started else Code.BASH_UNAVAILABLE
                ) from None
            finally:
                # shield cleanup from task cancellation; retain the resource slot if unconfirmed.
                cleanup = asyncio.create_task(self.remove(name, execution_id))
                try:
                    clean = await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    clean = await cleanup
                with self.db.session(write=True) as db:
                    row = db.get(BashExecution, execution_id)
                    if clean and outcome is not None:
                        row.state, row.finished_at = "finished", self.now()
                        if outcome.timed_out or (outcome.exit_code is None and outcome.truncated):
                            db.get(ConversationSession, row.session_id).workspace_unconfirmed = True
                    elif started:
                        self.unknown(db, row)
                        if clean:
                            row.state = "reaped"
                    else:
                        row.state = "reaped" if clean else "unknown"
                if clean:
                    self.pending.pop(execution_id, None)
                elif outcome is not None:
                    raise AppError(Code.TOOL_EXECUTION_UNKNOWN)
