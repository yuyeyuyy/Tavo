"""Trusted-owner host execution. Deliberately not a filesystem/network sandbox."""

import asyncio
import contextlib
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationInfo, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import select

from app.agent.bash import bash_result
from app.api.base import DTO
from app.core.errors import AppError, Code
from app.core.security import private_read
from app.core.startup import audit_tool
from app.models.entities import HostExecution, Secret
from app.repositories.sessions import identity, require_leader, require_scope

SUPERVISOR = Path(__file__).with_name("host_supervisor.py").resolve()


class HostBashArguments(DTO):
    command: Annotated[str, Field(min_length=1, max_length=32768)]
    cwd: Annotated[str, Field(min_length=1, max_length=4096)] = "."
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=300)] = 60

    @model_validator(mode="after")
    def bounded(self, info: ValidationInfo):
        policy = (info.context or {}).get("policy")
        host = policy.host if policy else None
        if host and "timeout_seconds" not in self.model_fields_set:
            self.timeout_seconds = host.default_timeout_seconds
        if len(self.command.encode()) > (host.command_max_bytes if host else 32768):
            raise PydanticCustomError("command_too_long", "COMMAND_TOO_LONG")
        if host and self.timeout_seconds > host.max_timeout_seconds:
            raise PydanticCustomError("resource_limit", "RESOURCE_LIMIT")
        if not self.command.strip() or "\x00" in self.command or "\x00" in self.cwd:
            raise ValueError("Invalid host command")
        return self


class OutputGuard:
    def __init__(self, secrets):
        self.needles = [s.encode() for s in secrets if s]
        self.needles.extend(
            [
                b"-----BEGIN PRIVATE KEY",
                b"-----BEGIN RSA PRIVATE KEY",
                b"-----BEGIN OPENSSH PRIVATE KEY",
            ]
        )
        self.size = max(map(len, self.needles), default=1)
        self.tails = {}
        self.denied = False

    def check(self, stream, data):
        value = self.tails.get(stream, b"") + data
        self.denied |= any(needle in value for needle in self.needles)
        self.tails[stream] = value[-self.size :]


class HostBashRunner:
    def __init__(self, settings, database, secrets, now):
        self.settings, self.db, self.secrets, self.now = settings, database, secrets, now
        self.policy = settings.sandbox.host
        self.active = {}
        self.available = bool(
            settings.host_bash_enabled
            and self.policy.enabled
            and sys.platform in {"darwin", "linux"}
            and Path("/bin/bash").is_file()
        )

    def busy_slots(self):
        with self.db.session() as db:
            return set(
                db.scalars(select(HostExecution.run_id).where(HostExecution.state != "finished"))
            )

    def protected(self):
        return [
            self.settings.data_dir.resolve(),
            self.settings.keyring_file.resolve(),
            self.settings.client_token_file.resolve(),
            *(Path.home() / p for p in (".ssh", ".aws", ".config/gcloud")),
        ]

    def validate(self, args, frozen):
        if not self.available:
            raise AppError(Code.HOST_UNAVAILABLE)
        if not frozen or not frozen.get("enabled"):
            raise AppError(Code.POLICY_DENIED)
        if len(args.command.encode()) > min(
            self.policy.command_max_bytes, frozen["command_max_bytes"]
        ):
            raise AppError(Code.COMMAND_TOO_LONG)
        if args.timeout_seconds > min(
            self.policy.max_timeout_seconds, frozen["max_timeout_seconds"]
        ):
            raise AppError(Code.RESOURCE_LIMIT)
        try:
            base = Path(frozen["default_cwd"])
            cwd = (base / args.cwd).resolve(strict=True)
            if not cwd.is_dir():
                raise ValueError
        except (OSError, ValueError):
            raise AppError(Code.INVALID_INPUT) from None
        for protected in self.protected():
            if cwd == protected or protected in cwd.parents or str(protected) in args.command:
                raise AppError(Code.POLICY_DENIED)
        if any(p in args.command for p in ("~/.ssh", "~/.aws", "$HOME/.ssh", "$HOME/.aws")):
            raise AppError(Code.POLICY_DENIED)
        return cwd

    def guard(self):
        _, ring = self.secrets.ring()
        values = []
        with self.db.session() as db:
            for row in db.scalars(select(Secret)):
                try:
                    values.append(ring.decrypt(row.ciphertext).decode())
                except Exception:
                    raise AppError(Code.CREDENTIAL_UNAVAILABLE) from None
        raw = json.loads(private_read(self.settings.keyring_file))
        values.extend(item["key"] for item in raw["keys"])
        values.append(private_read(self.settings.client_token_file).decode().strip())
        return OutputGuard(values)

    async def inspect(self, *args):
        proc = await asyncio.create_subprocess_exec(
            "/bin/ps", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 2)
            return proc.returncode, out.decode()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def owns(self, row):
        if not row.pid:
            return False
        rc, out = await self.inspect("-p", str(row.pid), "-o", "pgid=,command=")
        return (
            rc == 0
            and bool(out.split())
            and out.split()[0] == str(row.pid)
            and str(SUPERVISOR) in out
            and row.id in out
        )

    async def group_absent(self, pid):
        if not pid:
            return True
        if pid <= 1:
            return False
        try:
            os.killpg(pid, 0)  # Existence check only, never sends a signal.
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    async def reconcile(self, owner, fence):
        with self.db.session() as db:
            require_leader(db, owner, fence, self.now())
            rows = list(db.scalars(select(HostExecution).where(HostExecution.state != "finished")))
        for row in rows:
            if row.id in self.active:
                continue
            if row.state == "unknown_detached":
                continue
            if (row.worker_owner, row.worker_fence) == (owner, fence) and row.state != "unknown":
                continue
            if await self.owns(row):
                with self.db.session() as db:
                    require_leader(db, owner, fence, self.now())
                with contextlib.suppress(ProcessLookupError):
                    os.kill(
                        row.pid, signal.SIGTERM
                    )  # Ask the verified supervisor to clean its own group.
                await asyncio.sleep(self.policy.terminate_grace_seconds + 0.1)
            absent = await self.group_absent(row.pid)
            with self.db.session(write=True) as db:
                require_leader(db, owner, fence, self.now())
                current = db.get(HostExecution, row.id)
                current.state = "finished" if absent else "unknown"
                current.finished_at = self.now() if absent else None
                current.error_code = str(Code.TOOL_EXECUTION_UNKNOWN)

    async def execute(self, scope, call_id, args):
        started = time.monotonic()
        with self.db.session() as db:
            run = require_scope(db, scope, self.now())
            frozen = (run.context or {}).get("hostPolicy")
        cwd = self.validate(args, frozen)
        guard = self.guard()
        guard.check("command", args.command.encode())
        if guard.denied:
            raise AppError(Code.POLICY_DENIED)
        deadline = started + self.settings.sandbox.queue_timeout_seconds
        while True:
            with self.db.session(write=True) as db:
                run = require_scope(db, scope, self.now())
                if db.scalar(select(HostExecution.id).where(HostExecution.tool_call_id == call_id)):
                    raise AppError(Code.TOOL_EXECUTION_UNKNOWN)
                if not db.scalar(select(HostExecution.id).where(HostExecution.state != "finished")):
                    row = HostExecution(
                        id=identity(),
                        run_id=scope.run_id,
                        tool_call_id=call_id,
                        state="reserved",
                        slot=1,
                        worker_owner=scope.owner,
                        worker_fence=scope.fence,
                        created_at=self.now(),
                    )
                    db.add(row)
                    break
            if time.monotonic() >= deadline:
                raise AppError(Code.BUSY)
            await asyncio.sleep(0.05)
        active_seconds = min(
            self.settings.sandbox.max_active_seconds,
            (run.context or {})
            .get("sandboxPolicy", {})
            .get("max_active_seconds", self.settings.sandbox.max_active_seconds),
        )
        timeout = min(args.timeout_seconds, max(0.001, active_seconds - run.active_ms / 1000))
        read_fd, write_fd = os.pipe()
        proc = None
        reader_transport = None
        tasks = []
        buffer = {"out": bytearray(), "err": bytearray()}
        truncated = False
        error, rc, clean = None, None, False
        watchdog = min(self.policy.watchdog_seconds, max(0.05, self.settings.lease_seconds / 3))
        heartbeat_interval = min(self.policy.heartbeat_seconds, watchdog / 3)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(SUPERVISOR),
                row.id,
                str(write_fd),
                str(self.policy.terminate_grace_seconds),
                str(watchdog),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                pass_fds=(write_fd,),
                start_new_session=True,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            )
            self.active[row.id] = proc
            row.pid = proc.pid
            with self.db.session(write=True) as db:
                require_scope(db, scope, self.now())
                current = db.get(HostExecution, row.id)
                current.pid, current.state = proc.pid, "running"
            audit_tool("host-execution", "STARTED", call_id)
            os.close(write_fd)
            write_fd = None
            reader = asyncio.StreamReader(limit=1024)
            reader_transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(read_fd, "rb", buffering=0)
            )
            read_fd = None
            environment = {
                "PATH": os.environ.get("PATH", os.defpath),
                "HOME": str(Path.home()),
                "LANG": "C.UTF-8",
                "TMPDIR": tempfile.gettempdir(),
            }
            proc.stdin.write(
                json.dumps(
                    {"command": args.command, "cwd": str(cwd), "environment": environment},
                    ensure_ascii=False,
                ).encode()
                + b"\n"
            )
            await proc.stdin.drain()

            async def drain(name, stream):
                nonlocal truncated
                while data := await stream.read(8192):
                    guard.check(name, data)
                    remaining = min(self.policy.capture_bytes, frozen["capture_bytes"]) - len(
                        buffer[name]
                    )
                    buffer[name].extend(data[: max(0, remaining)])
                    truncated |= len(data) > remaining

            async def heartbeat():
                while True:
                    with self.db.session() as db:
                        require_scope(db, scope, self.now())
                    proc.stdin.write(b"alive\n")
                    await proc.stdin.drain()
                    await asyncio.sleep(heartbeat_interval)

            async def run_command():
                beat = asyncio.create_task(heartbeat())
                wait = asyncio.create_task(proc.wait())
                tasks.extend([beat, wait])
                done, _ = await asyncio.wait([beat, wait], return_when=asyncio.FIRST_COMPLETED)
                if beat in done:
                    await beat
                await wait

            tasks.extend(
                [
                    asyncio.create_task(drain("out", proc.stdout)),
                    asyncio.create_task(drain("err", proc.stderr)),
                ]
            )
            try:
                await asyncio.wait_for(run_command(), timeout)
                value = await asyncio.wait_for(reader.readline(), 1)
                if value:
                    rc = json.loads(value)["exitCode"]
                error = None if rc == 0 else Code.EXECUTION_ERROR
            except TimeoutError:
                error = Code.TIMEOUT
            except (BrokenPipeError, ConnectionResetError):
                if proc.returncode is None:
                    raise
                value = await asyncio.wait_for(reader.readline(), 1)
                rc = json.loads(value)["exitCode"] if value else None
                error = None if rc == 0 else Code.EXECUTION_ERROR
        except asyncio.CancelledError:
            error = Code.CANCELLED
            raise
        except AppError as failure:
            error = failure.code
            raise
        except (OSError, ValueError, KeyError):
            error = Code.EXECUTION_ERROR
        finally:

            async def cleanup():
                nonlocal clean, error
                detached = False
                if proc:
                    if proc.stdin:
                        proc.stdin.close()
                    try:
                        await asyncio.wait_for(
                            proc.wait(),
                            self.policy.watchdog_seconds + self.policy.terminate_grace_seconds + 2,
                        )
                    except TimeoutError:
                        if await self.owns(row):
                            os.killpg(proc.pid, signal.SIGKILL)
                            await proc.wait()
                    # No PID-only recovery kill; unknown or escaped processes retain the slot.
                    clean = proc.returncode is not None and await self.group_absent(proc.pid)
                else:
                    clean = True
                if tasks:
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*tasks[:2], return_exceptions=True), 1
                        )
                    except TimeoutError:
                        clean = False
                        detached = True
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                if reader_transport:
                    reader_transport.close()
                for fd in (read_fd, write_fd):
                    if fd is not None:
                        os.close(fd)
                with self.db.session(write=True) as db:
                    current = db.get(HostExecution, row.id)
                    if current and (current.worker_owner, current.worker_fence) == (
                        scope.owner,
                        scope.fence,
                    ):
                        current.state = (
                            "finished" if clean else "unknown_detached" if detached else "unknown"
                        )
                        current.finished_at = self.now() if clean else None
                        if guard.denied:
                            error = Code.POLICY_DENIED
                        current.error_code = str(error) if error else None
                self.active.pop(row.id, None)
                audit_tool("host-cleanup", "CONFIRMED" if clean else "UNKNOWN", call_id)

            cleanup_task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                await cleanup_task
                raise
        if not clean:
            raise AppError(Code.TOOL_EXECUTION_UNKNOWN)
        try:
            current_guard = self.guard()  # Keys may have changed while the command was running.
            for stream, value in buffer.items():
                current_guard.check(stream, bytes(value))
            guard.denied |= current_guard.denied
        except AppError:
            error = Code.CREDENTIAL_UNAVAILABLE
            buffer = {"out": bytearray(), "err": bytearray()}
        if guard.denied:
            error = Code.POLICY_DENIED
            buffer = {"out": bytearray(), "err": bytearray()}
        with self.db.session(write=True) as db:
            require_scope(db, scope, self.now())
            db.get(HostExecution, row.id).error_code = str(error) if error else None
        limit = min(self.policy.public_output_bytes, frozen["public_output_bytes"])
        result = bash_result(
            error,
            exit_code=rc,
            stdout=bytes(buffer["out"][:limit]).decode("utf-8", "replace"),
            stderr=bytes(buffer["err"][:limit]).decode("utf-8", "replace"),
            duration_ms=int((time.monotonic() - started) * 1000),
            truncated=truncated or any(len(v) > limit for v in buffer.values()),
        )
        result.execution_target, result.execution_id = "host", row.id
        return result, str(error) if error else None

    async def close(self):
        for proc in list(self.active.values()):
            if proc.stdin:
                proc.stdin.close()
        for key, proc in list(self.active.items()):
            try:
                await asyncio.wait_for(
                    proc.wait(),
                    self.policy.watchdog_seconds + self.policy.terminate_grace_seconds + 2,
                )
            except TimeoutError:
                with self.db.session() as db:
                    row = db.get(HostExecution, key)
                if row and await self.owns(row):
                    os.killpg(proc.pid, signal.SIGKILL)
                    await asyncio.wait_for(proc.wait(), 2)
