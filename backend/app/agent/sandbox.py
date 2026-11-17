"""Owns Docker resources; untrusted code never owns this manager or its control channel."""

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import select

from app.agent.bash import bash_result
from app.agent.egress import EgressCoordinator
from app.core.errors import AppError, Code
from app.core.sandbox_config import SandboxPolicy
from app.core.startup import audit_tool
from app.models.entities import (
    AssistantReview,
    AssistantRun,
    AssistantToolCall,
    ConversationSession,
    SandboxInstance,
    SandboxOperation,
    WorkerLease,
    WorkspaceCheckpoint,
)
from app.repositories.sessions import require_leader, require_scope


class SandboxManager:
    def __init__(self, runner, secrets):
        self.runner, self.db, self.now = runner, runner.db, runner.now
        self.policy = runner.settings.sandbox
        self.egress = EgressCoordinator(self, secrets)
        self.lock = asyncio.Lock()
        self.executing = set()

    def busy_slots(self):
        with self.db.session() as db:
            return set(
                db.scalars(
                    select(SandboxInstance.run_id).where(
                        SandboxInstance.state == "unknown", SandboxInstance.run_id.is_not(None)
                    )
                )
            )

    def audit(self, stage, code, call_id):
        audit_tool(stage, code, call_id)

    @asynccontextmanager
    async def serialize(self):
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=self.policy.queue_timeout_seconds)
        except TimeoutError:
            raise AppError(Code.BUSY) from None
        try:
            yield
        finally:
            self.lock.release()

    def policy_for(self, run_id):
        if run_id is None:
            return self.policy
        with self.db.session() as db:
            run = db.get(AssistantRun, run_id)
            frozen = (run.context or {}).get("sandboxPolicy") if run else None
        value = self.policy.model_dump()
        if frozen:
            for name, current in value.items():
                if (
                    isinstance(current, (int, float))
                    and not isinstance(current, bool)
                    and name != "schema_version"
                ):
                    value[name] = min(current, frozen.get(name, current))
        return SandboxPolicy.model_validate(value)

    async def claim_warmup(self, name, execution_id):
        deadline = time.monotonic() + self.policy.queue_timeout_seconds
        while True:
            recovery = False
            with self.db.session(write=True) as db:
                current = db.scalar(
                    select(SandboxInstance).where(SandboxInstance.state != "closed")
                )
                if current is None:
                    db.add(
                        SandboxInstance(
                            id=execution_id,
                            slot=1,
                            container_name=name,
                            state="warmup",
                            worker_owner="startup",
                            worker_fence=0,
                            resource_profile="default",
                            created_at=self.now(),
                            last_used_at=self.now(),
                        )
                    )
                    return
                lease = db.get(WorkerLease, 1)
                if current.run_id and lease and lease.expires_at <= self.now():
                    from app.repositories.time import plus_seconds

                    lease.owner = "startup-" + execution_id
                    lease.fence += 1
                    lease.expires_at = plus_seconds(self.now(), self.runner.settings.lease_seconds)
                    recovery = True
            if recovery:
                try:
                    clean = await self.close_instance(current, preserve_work=True)
                    with self.db.session(write=True) as db:
                        require_leader(db, "startup-" + execution_id, lease.fence, self.now())
                        run = db.get(AssistantRun, current.run_id)
                        if run.status == "running":
                            from app.repositories.sessions import SessionRepository

                            db.get(
                                ConversationSession, current.session_id
                            ).workspace_unconfirmed = True
                            SessionRepository(self.db, self.now).finish(
                                db, run, "interrupted", str(Code.TOOL_EXECUTION_UNKNOWN)
                            )
                    if not clean:
                        raise AppError(Code.SANDBOX_ERROR)
                finally:
                    with self.db.session(write=True) as db:
                        stored = db.get(WorkerLease, 1)
                        if (
                            stored.owner == "startup-" + execution_id
                            and stored.fence == lease.fence
                        ):
                            stored.owner, stored.expires_at = "", self.now()
                continue
            # A previous startup may have died after creating its warmup. Only this fixed
            # command's resources are eligible here; never reap another running task.
            if current.run_id is None and current.created_at < self.now():
                from datetime import datetime

                age = (
                    datetime.fromisoformat(self.now().replace("Z", "+00:00"))
                    - datetime.fromisoformat(current.created_at.replace("Z", "+00:00"))
                ).total_seconds()
                if age > self.policy.warmup_timeout_seconds + 30:
                    await self.close_instance(current)
            if time.monotonic() > deadline:
                raise AppError(Code.BUSY)
            await asyncio.sleep(0.1)

    def finish_warmup(self, execution_id, clean):
        with self.db.session(write=True) as db:
            row = db.get(SandboxInstance, execution_id)
            if row:
                row.state = "closed" if clean else "unknown"

    async def warmup_network(self, execution_id):
        if self.policy.network.mode != "proxy":
            return None
        with self.db.session(write=True) as db:
            row = db.get(SandboxInstance, execution_id)
            row.gateway_name = row.container_name + "-egress"
            row.socket_volume = "tavo-egress-" + self.runner.instance + "-" + row.id
        await self.start_gateway(row, isolated=True)
        return row.socket_volume

    async def clean_warmup_network(self, execution_id):
        with self.db.session() as db:
            row = db.get(SandboxInstance, execution_id)
        return await self.close_instance(row) if row and row.gateway_name else True

    async def volume(self, name, labels):
        rc, raw, _ = await self.runner.cli(
            "volume", "inspect", "--format", "{{json .Labels}}", name
        )
        existed = rc == 0
        if not existed:
            args = ["volume", "create"]
            for key, value in labels.items():
                args.extend(["--label", key + "=" + value])
            rc, _, _ = await self.runner.cli(*args, name)
            if rc:
                raise AppError(Code.SANDBOX_ERROR)
            rc, raw, _ = await self.runner.cli(
                "volume", "inspect", "--format", "{{json .Labels}}", name
            )
        actual = json.loads(raw)
        if rc or not isinstance(actual, dict) or any(actual.get(k) != v for k, v in labels.items()):
            raise AppError(Code.POLICY_DENIED)
        return existed

    async def acquire(self, scope, profile="default"):
        policy = self.policy_for(scope.run_id)
        deadline = time.monotonic() + policy.queue_timeout_seconds
        while True:
            with self.db.session(write=True) as db:
                require_scope(db, scope, self.now())
                current = db.scalar(
                    select(SandboxInstance).where(SandboxInstance.state != "closed")
                )
                if (
                    current
                    and current.run_id == scope.run_id
                    and current.worker_owner == scope.owner
                    and current.worker_fence == scope.fence
                    and current.state == "idle"
                ):
                    current.last_used_at = self.now()
                    row = current
                    break
                if current is None:
                    previous = db.scalar(
                        select(SandboxInstance)
                        .where(SandboxInstance.run_id == scope.run_id)
                        .order_by(SandboxInstance.created_at)
                        .limit(1)
                    )
                    checkpoint = db.get(WorkspaceCheckpoint, scope.session_id)
                    if checkpoint is None:
                        checkpoint = WorkspaceCheckpoint(session_id=scope.session_id)
                        db.add(checkpoint)
                    key = uuid4().hex
                    prefix = "tavo-sandbox-" + self.runner.instance + "-"
                    work = (
                        "tavo-work-"
                        + self.runner.instance
                        + "-"
                        + hashlib.sha256(scope.run_id.encode()).hexdigest()[:24]
                    )
                    row = SandboxInstance(
                        id=key,
                        slot=1,
                        run_id=scope.run_id,
                        session_id=scope.session_id,
                        container_name=prefix + key,
                        work_volume=work,
                        state="preparing",
                        worker_owner=scope.owner,
                        worker_fence=scope.fence,
                        baseline_revision=previous.baseline_revision
                        if previous
                        else checkpoint.revision,
                        resource_profile=profile,
                        created_at=self.now(),
                        last_used_at=self.now(),
                    )
                    if self.policy.network.mode == "proxy":
                        row.socket_volume = "tavo-egress-" + self.runner.instance + "-" + key
                        row.gateway_name = prefix + key + "-egress"
                    db.add(row)
                    current_revision = checkpoint.revision
                    break
            if current and current.state == "idle" and current.run_id != scope.run_id:
                with self.db.session() as db:
                    require_scope(db, scope, self.now())
                await self.close_instance(current, preserve_work=True)
                continue
            if time.monotonic() > deadline:
                raise AppError(Code.BUSY)
            await asyncio.sleep(0.1)
        if row.state == "idle":
            if profile != row.resource_profile:
                memory = policy.max_memory_mib if profile == "expanded" else policy.memory_mib
                cpu = policy.max_cpu if profile == "expanded" else policy.cpu
                rc, _, _ = await self.runner.cli(
                    "update",
                    f"--cpus={cpu}",
                    f"--memory={memory}m",
                    f"--memory-swap={memory}m",
                    row.container_name,
                )
                if rc:
                    raise AppError(Code.RESOURCE_LIMIT)
                with self.db.session(write=True) as db:
                    require_scope(db, scope, self.now())
                    db.get(SandboxInstance, row.id).resource_profile = profile
            return row
        try:
            await self.runner.probe()
            if not self.runner.available:
                raise AppError(Code.SANDBOX_ERROR)
            await self.runner.workspace(scope.session_id)  # legacy volume identity is unchanged
            existed = await self.volume(
                row.work_volume, {"tavo.instance": self.runner.instance, "tavo.run": scope.run_id}
            )
            if not existed:
                await self.workspace(row, "initialize", revision=current_revision)
            args = self.runner.container_options(
                row.container_name, row.id, profile=profile, policy=policy
            )
            args.extend(
                [
                    "--init",
                    "--label",
                    "tavo.kind=execution",
                    "--mount",
                    "type=volume,src=" + row.work_volume + ",dst=/workspace",
                    "--env",
                    "PYTHONPATH=/workspace/.deps",
                    "--env",
                    f"TAVO_INSTALL_BYTES={self.policy.network.install_bytes}",
                    "--env",
                    f"TAVO_INSTALL_FILES={self.policy.network.install_files}",
                    "--env",
                    f"TAVO_INSTALL_TIMEOUT={self.policy.max_timeout_seconds}",
                ]
            )
            if row.gateway_name:
                await self.start_gateway(row)
                args.extend(
                    [
                        "--mount",
                        "type=volume,src=" + row.socket_volume + ",dst=/run/tavo-egress,readonly",
                        "--env",
                        "TAVO_EGRESS_SOCKET=/run/tavo-egress/proxy.sock",
                    ]
                )
            rc, _, _ = await self.runner.cli(
                "run",
                "--detach",
                *args,
                self.runner.image_id,
                "python",
                "-I",
                "-m",
                "tavo_sandbox.supervisor",
            )
            if rc:
                raise AppError(Code.SANDBOX_ERROR)
            with self.db.session(write=True) as db:
                require_scope(db, scope, self.now())
                db.get(SandboxInstance, row.id).state = "idle"
            row.state = "idle"
            return row
        except BaseException:
            await asyncio.shield(self.close_instance(row))
            raise

    async def workspace(self, row, action, **extra):
        policy = self.policy_for(row.run_id)
        name, token = "tavo-workspace-helper-" + uuid4().hex, uuid4().hex
        value = {
            "action": action,
            "limits": policy.model_dump(),
            "revision": row.baseline_revision,
            **extra,
        }
        args = self.runner.container_options(name, token, policy=policy)
        args.extend(
            [
                "--label",
                "tavo.kind=workspace-helper",
                "--label",
                "tavo.parent=" + row.id,
                "--mount",
                "type=volume,src="
                + self.runner.volume(row.session_id)
                + ",dst=/source"
                + ("" if action in {"commit", "gc"} else ",readonly"),
                "--mount",
                "type=volume,src="
                + row.work_volume
                + ",dst=/workspace"
                + ("" if action == "initialize" else ",readonly"),
            ]
        )
        try:
            rc, raw, _ = await self.runner.cli(
                "run",
                "--rm",
                "--interactive",
                *args,
                self.runner.image_id,
                "python",
                "-I",
                "-m",
                "tavo_sandbox.workspace",
                input_data=json.dumps(value).encode(),
                timeout=self.policy.max_timeout_seconds,
            )
            if rc:
                raise AppError(Code.SANDBOX_ERROR)
            result = json.loads(raw)
            if result.get("errorCode"):
                raise AppError(Code(result["errorCode"]))
            return result
        finally:
            if not await asyncio.shield(self.runner.remove(name, token)):
                with self.db.session(write=True) as db:
                    db.get(SandboxInstance, row.id).state = "unknown"
                raise AppError(Code.SANDBOX_ERROR)

    async def control(self, row, value, timeout=10):
        if not row.gateway_name:
            raise AppError(Code.NETWORK_DENIED)
        rc, raw, _ = await self.runner.cli(
            "exec",
            "--interactive",
            row.gateway_name,
            "python",
            "-I",
            "-m",
            "tavo_sandbox.control",
            input_data=json.dumps(value).encode(),
            timeout=timeout,
            limit=4194304,
        )
        if rc:
            raise AppError(Code.SANDBOX_ERROR)
        return json.loads(raw)

    async def start_gateway(self, row, isolated=False):
        await self.volume(
            row.socket_volume, {"tavo.instance": self.runner.instance, "tavo.execution": row.id}
        )
        # Only this trusted application proxy has an Internet route. It never executes scripts.
        args = self.runner.container_options(row.gateway_name, row.id)
        args = [arg for arg in args if arg != "--network=none"]
        args.extend(
            [
                "--network=none" if isolated else "--network=bridge",
                "--label",
                "tavo.kind=egress",
                "--tmpfs",
                "/run/private:rw,noexec,nosuid,size=134217728,uid=10001,gid=10001,mode=0700",
                "--mount",
                "type=volume,src=" + row.socket_volume + ",dst=/run/tavo-egress",
            ]
        )
        # Named volume initialization must copy an image-owned UID 10001 directory.
        rc, _, _ = await self.runner.cli(
            "run",
            "--detach",
            *args,
            self.runner.image_id,
            "python",
            "-I",
            "-m",
            "tavo_sandbox.egress",
        )
        if rc:
            raise AppError(Code.SANDBOX_ERROR)
        configuration = self.egress.configuration(row.run_id)
        if row.run_id:
            self.egress.reserve(row, configuration)
        await self.control(row, {"action": "configure", "config": configuration})
        await self.egress.restore_results(row)
        for _ in range(100):
            rc, _, _ = await self.runner.cli(
                "exec",
                row.gateway_name,
                "python",
                "-I",
                "-c",
                "import os,sys;sys.exit(not os.path.exists('/run/tavo-egress/proxy.sock'))",
            )
            if not rc:
                return
            await asyncio.sleep(0.05)
        raise AppError(Code.SANDBOX_ERROR)

    async def stats(self, row):
        script = "import json;from pathlib import Path; r={};\nfor n in ('memory.events','pids.events'):\n p=Path('/sys/fs/cgroup')/n; r[n]=dict(l.split() for l in p.read_text().splitlines()) if p.exists() else {}\nprint(json.dumps(r))"
        rc, raw, _ = await self.runner.cli("exec", row.container_name, "python", "-I", "-c", script)
        return json.loads(raw) if rc == 0 else {}

    async def no_children(self, row):
        rc, raw, _ = await self.runner.cli("top", row.container_name, "-eo", "pid")
        return rc == 0 and len(raw.strip().splitlines()) == 3  # Docker init + fixed supervisor

    async def monitor(self, row):
        while True:
            await asyncio.sleep(1)
            # Fixed stat-only scanner: no imports from /workspace and no file contents in logs.
            code = (
                "import os,sys; n=s=0\nfor d,ds,fs in os.walk('/workspace',followlinks=False):\n n+=len(ds)+len(fs)\n for f in fs:\n  try:s+=os.lstat(os.path.join(d,f)).st_size\n  except FileNotFoundError:pass\n if n>"
                + str(self.policy.workspace_max_files)
                + " or s>"
                + str(self.policy.workspace_max_bytes)
                + ":sys.exit(77)"
            )
            rc, _, _ = await self.runner.cli(
                "exec", row.container_name, "python", "-I", "-c", code, timeout=5
            )
            if rc == 77:
                raise AppError(Code.RESOURCE_LIMIT)

    async def execute(self, scope, call_id, args, *, kind="bash"):
        async with self.serialize():
            policy = self.policy_for(scope.run_id)
            if kind == "bash":
                if len(args.command.encode()) > policy.command_max_bytes:
                    raise AppError(Code.COMMAND_TOO_LONG)
                if args.timeout_seconds > policy.max_timeout_seconds:
                    raise AppError(Code.RESOURCE_LIMIT)
            row = await self.acquire(scope, getattr(args, "resource_profile", "default"))
            started = time.monotonic()
            with self.db.session(write=True) as db:
                run = require_scope(db, scope, self.now())
                remaining = max(0.001, policy.max_active_seconds - run.active_ms / 1000)
                call = db.get(AssistantToolCall, call_id)
                session = db.get(ConversationSession, scope.session_id)
                modern = (run.context or {}).get("policyVersion") in {"4", "5"}
                if (
                    session.workspace_unconfirmed
                    and not modern
                    and not db.scalar(
                        select(AssistantReview.id).where(
                            AssistantReview.tool_call_id == call_id,
                            AssistantReview.source == "human",
                            AssistantReview.action == "allow",
                        )
                    )
                ):
                    raise AppError(Code.APPROVAL_REQUIRED)
                previous = db.get(SandboxOperation, call_id)
                if previous:
                    raise AppError(Code.TOOL_EXECUTION_UNKNOWN)
                db.add(
                    SandboxOperation(
                        tool_call_id=call_id,
                        sandbox_id=row.id,
                        kind=kind,
                        state="running",
                        argument_digest=call.argument_digest,
                        created_at=self.now(),
                    )
                )
                db.get(SandboxInstance, row.id).state = "running"
            self.audit("execution", "STARTED", call_id)
            self.executing.add(row.id)
            try:
                before = await self.stats(row)
                if row.gateway_name:
                    await self.control(row, {"action": "begin"})
            except BaseException:
                self.executing.discard(row.id)
                await asyncio.shield(self.close_instance(row, preserve_work=True))
                raise
            error, rc, out, err, truncated = None, None, b"", b"", False
            watcher = asyncio.create_task(self.monitor(row))

            async def invoke():
                with self.db.session() as db:
                    require_scope(db, scope, self.now())
                if kind == "workspace_write":
                    value = {
                        **args.model_dump(by_alias=True),
                        "action": "write",
                        "max_file_bytes": self.policy.max_file_bytes,
                    }
                    return await self.runner.cli(
                        "exec",
                        "--interactive",
                        row.container_name,
                        "python",
                        "-I",
                        "-m",
                        "tavo_sandbox.workspace",
                        input_data=json.dumps(value).encode(),
                        timeout=min(policy.default_timeout_seconds, remaining),
                    )
                script = 'cd -P -- "$1" || exit 126; case "$PWD" in /workspace|/workspace/*) exec /bin/bash --noprofile --norc -c "$2";; *) exit 126;; esac'
                return await self.runner.cli(
                    "exec",
                    row.container_name,
                    "/bin/bash",
                    "--noprofile",
                    "--norc",
                    "-c",
                    script,
                    "tavo-bash",
                    args.cwd,
                    args.command,
                    timeout=min(args.timeout_seconds, remaining),
                    limit=self.policy.capture_bytes,
                    truncate=True,
                )

            command = asyncio.create_task(invoke())
            try:
                done, _ = await asyncio.wait(
                    {command, watcher}, return_when=asyncio.FIRST_COMPLETED
                )
                if watcher in done:
                    await watcher
                rc, out, err = await command
                if kind == "workspace_write":
                    metadata = json.loads(out)
                    if metadata.get("errorCode"):
                        error = Code(metadata["errorCode"])
                if rc and not error:
                    error = Code.EXECUTION_ERROR
                after = await self.stats(row)
                for group, counter in (("memory.events", "oom_kill"), ("pids.events", "max")):
                    if int(after.get(group, {}).get(counter, 0)) > int(
                        before.get(group, {}).get(counter, 0)
                    ):
                        error = Code.RESOURCE_LIMIT
                await self.egress.collect(scope, call_id, row)
                if row.gateway_name:
                    network_errors = await self.control(row, {"action": "errors"})
                    for network_error in network_errors:
                        self.audit("network", network_error, call_id)
                    if rc and network_errors:
                        error = Code(network_errors[-1])
                if not await self.no_children(row):
                    # Remove the whole cgroup, including detached/grandchild processes. Files
                    # stay in the run volume. No container is reused until that is confirmed.
                    if not await self.close_instance(row, preserve_work=True):
                        raise AppError(Code.SANDBOX_ERROR)
                diff = await self.workspace(row, "inspect")
                truncated = (
                    len(out) >= self.policy.public_output_bytes
                    or len(err) >= self.policy.public_output_bytes
                )
                result = bash_result(
                    error,
                    exit_code=rc,
                    stdout=out[: self.policy.public_output_bytes].decode("utf-8", "replace"),
                    stderr=err[: self.policy.public_output_bytes].decode("utf-8", "replace"),
                    duration_ms=int((time.monotonic() - started) * 1000),
                    sandbox_id=row.id,
                    truncated=truncated,
                )
                with self.db.session(write=True) as db:
                    current_run = require_scope(db, scope, self.now())
                    op = db.get(SandboxOperation, call_id)
                    op.output = result.model_dump(by_alias=True)
                    op.approval_digest = diff["digest"]
                    op.approval_detail = diff
                    needs_confirmation = not modern or (
                        kind == "bash"
                        and (current_run.context or {}).get("reviewMode") == "request_approval"
                    )
                    op.state = (
                        "awaiting_commit"
                        if needs_confirmation
                        and (
                            diff["deleted"] >= self.policy.delete_approval_files
                            or diff["changed"] >= self.policy.overwrite_approval_files
                        )
                        else "committing"
                    )
                    needs_approval = op.state == "awaiting_commit"
                if needs_approval:
                    self.audit("approval", Code.APPROVAL_REQUIRED, call_id)
                    return bash_result(
                        Code.APPROVAL_REQUIRED,
                        exit_code=rc,
                        stdout=json.dumps(
                            {
                                "deleted": diff["deleted"],
                                "changed": diff["changed"],
                                "diffDigest": diff["digest"],
                            }
                        ),
                        sandbox_id=row.id,
                        duration_ms=result.duration_ms,
                    ), Code.APPROVAL_REQUIRED
                await self.commit(scope, row, call_id, diff["digest"])
                self.audit("execution", error or "SUCCEEDED", call_id)
                return result, str(error) if error else None
            except asyncio.CancelledError:
                error = Code.CANCELLED
                self.record_cancel(scope, call_id, row.id, started)
                raise
            except TimeoutError:
                error = Code.TIMEOUT
                return bash_result(
                    error, sandbox_id=row.id, duration_ms=int((time.monotonic() - started) * 1000)
                ), str(error)
            except AppError as failure:
                error = failure.code
                return bash_result(
                    error, sandbox_id=row.id, duration_ms=int((time.monotonic() - started) * 1000)
                ), str(error)
            except (OSError, ValueError, TypeError):
                error = Code.SANDBOX_ERROR
                return bash_result(
                    error, sandbox_id=row.id, duration_ms=int((time.monotonic() - started) * 1000)
                ), str(error)
            finally:
                command.cancel()
                watcher.cancel()
                await asyncio.gather(command, watcher, return_exceptions=True)
                self.executing.discard(row.id)
                if error in {Code.TIMEOUT, Code.CANCELLED, Code.RESOURCE_LIMIT, Code.SANDBOX_ERROR}:
                    with self.db.session(write=True) as db:
                        try:
                            require_leader(db, scope.owner, scope.fence, self.now())
                            operation = db.get(SandboxOperation, call_id)
                            if operation.state != "finished":
                                db.get(
                                    ConversationSession, scope.session_id
                                ).workspace_unconfirmed = True
                        except AppError as failure:
                            if failure.code != Code.LEASE_LOST:
                                raise
                    await asyncio.shield(self.close_instance(row, preserve_work=True))
                with self.db.session(write=True) as db:
                    stored = db.get(SandboxInstance, row.id)
                    if stored.state == "running":
                        stored.state, stored.last_used_at = "idle", self.now()

    async def commit(self, scope, row, call_id, fingerprint):
        revision = uuid4().hex
        with self.db.session() as db:
            require_scope(db, scope, self.now())
            old = db.get(WorkspaceCheckpoint, scope.session_id).revision
        await self.workspace(row, "commit", expected_digest=fingerprint, new_revision=revision)
        with self.db.session(write=True) as db:
            require_scope(db, scope, self.now())
            pointer = db.get(WorkspaceCheckpoint, scope.session_id)
            if pointer.revision != old:
                raise AppError(Code.REVISION_CONFLICT)
            pointer.revision = revision
            pointer.retained_revisions = [*pointer.retained_revisions, revision]
            retained = pointer.retained_revisions[-self.policy.retained_versions :]
            if row.baseline_revision:
                retained.append(row.baseline_revision)
            obsolete = [v for v in pointer.retained_revisions if v not in retained]
            op = db.get(SandboxOperation, call_id)
            op.state, op.finished_at = "finished", self.now()
        if obsolete:
            await self.workspace(row, "gc", remove_revisions=obsolete)
            with self.db.session(write=True) as db:
                require_scope(db, scope, self.now())
                pointer = db.get(WorkspaceCheckpoint, scope.session_id)
                pointer.retained_revisions = [
                    v for v in pointer.retained_revisions if v not in obsolete
                ]

    def record_cancel(self, scope, call_id, sandbox_id, started):
        from app.models.entities import AssistantToolResult

        with self.db.session(write=True) as db:
            try:
                require_leader(db, scope.owner, scope.fence, self.now())
            except AppError:
                return
            result = db.scalar(
                select(AssistantToolResult).where(
                    AssistantToolResult.tool_call_id == call_id,
                    AssistantToolResult.status == "cancelled",
                )
            )
            if result:
                result.output = bash_result(
                    Code.CANCELLED,
                    sandbox_id=sandbox_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                ).model_dump(by_alias=True)

    async def resume_commit(self, scope, call_id):
        async with self.serialize():
            return await self._resume_commit(scope, call_id)

    async def _resume_commit(self, scope, call_id):
        with self.db.session() as db:
            require_scope(db, scope, self.now())
            op = db.get(SandboxOperation, call_id)
            if (
                not op
                or op.state != "awaiting_commit"
                or not db.scalar(
                    select(AssistantReview.id).where(
                        AssistantReview.tool_call_id == call_id,
                        AssistantReview.source == "human",
                        AssistantReview.action == "allow",
                        AssistantReview.summary == op.approval_digest,
                    )
                )
            ):
                raise AppError(Code.POLICY_DENIED)
            result = op.output
            fingerprint = op.approval_digest
        current = await self.acquire(scope)
        await self.commit(scope, current, call_id, fingerprint)
        return result, result.get("errorCode")

    async def close_instance(self, row, preserve_work=False):
        rc, children, _ = await self.runner.cli(
            "ps",
            "--all",
            "--filter",
            "label=tavo.instance=" + self.runner.instance,
            "--filter",
            "label=tavo.parent=" + row.id,
            "--format",
            '{{.Names}} {{.Label "tavo.execution"}}',
        )
        clean = rc == 0
        if clean:
            import re

            for line in children.decode().splitlines():
                name, token = line.split()
                if not re.fullmatch(
                    r"tavo-workspace-helper-[a-f0-9]{32}", name
                ) or not re.fullmatch(r"[a-f0-9]{32}", token):
                    raise AppError(Code.POLICY_DENIED)
                clean = await self.runner.remove(name, token) and clean
        clean = await self.runner.remove(row.container_name, row.id) and clean
        if row.gateway_name:
            if clean and row.run_id:
                try:
                    usage = await self.control(
                        row, {"action": "seal"}, timeout=self.policy.network.request_seconds + 5
                    )
                    self.egress.store_usage(row, usage, release=True)
                except (AppError, OSError, TimeoutError, ValueError):
                    pass  # retain reserved quota when final usage cannot be proven
            clean = await self.runner.remove(row.gateway_name, row.id) and clean
        if clean and row.socket_volume:
            rc, _, _ = await self.runner.cli("volume", "rm", row.socket_volume)
            clean = rc == 0
            if not clean:
                # Shutdown can be interrupted after Docker removed the socket volume but
                # before SQLite marked the instance closed. Prove absence on retry;
                # a failed rm alone cannot distinguish absence from an Engine failure.
                rc, names, _ = await self.runner.cli(
                    "volume", "ls", "--filter", "name=" + row.socket_volume, "--format", "{{.Name}}"
                )
                clean = rc == 0 and row.socket_volume not in names.decode().splitlines()
        with self.db.session(write=True) as db:
            stored = db.get(SandboxInstance, row.id)
            if stored:
                stored.state = "closed" if clean else "unknown"
        # Run work volumes are retained for recovery and pending commits; never delete user
        # task files during an uncertain/partial execution. Explicit GC is retention-based.
        return clean

    async def reconcile(self, owner, fence, only_owned=False):
        with self.db.session() as db:
            if not only_owned:
                require_leader(db, owner, fence, self.now())
            rows = list(
                db.scalars(
                    select(SandboxInstance).where(
                        SandboxInstance.state != "closed", SandboxInstance.run_id.is_not(None)
                    )
                )
            )
        from datetime import datetime

        for row in rows:
            if row.id in self.executing:
                continue
            if only_owned and (row.worker_owner != owner or row.worker_fence != fence):
                continue
            with self.db.session() as db:
                run = db.get(AssistantRun, row.run_id)
                age = (
                    datetime.fromisoformat(self.now().replace("Z", "+00:00"))
                    - datetime.fromisoformat(row.last_used_at.replace("Z", "+00:00"))
                ).total_seconds()
                obsolete = (
                    only_owned
                    or row.worker_owner != owner
                    or row.worker_fence != fence
                    or run.status != "running"
                    or age > self.policy.idle_seconds
                )
            if obsolete:
                await self.close_instance(row, preserve_work=True)

    async def finish_run(self, scope):
        with self.db.session() as db:
            rows = list(
                db.scalars(
                    select(SandboxInstance).where(
                        SandboxInstance.run_id == scope.run_id,
                        SandboxInstance.worker_owner == scope.owner,
                        SandboxInstance.worker_fence == scope.fence,
                    )
                )
            )
            run = db.get(AssistantRun, scope.run_id)
            uncertain = db.scalar(
                select(SandboxOperation.tool_call_id)
                .join(AssistantToolCall, SandboxOperation.tool_call_id == AssistantToolCall.id)
                .where(
                    AssistantToolCall.run_id == scope.run_id, SandboxOperation.state != "finished"
                )
            )
            discard = (
                run
                and run.status in {"succeeded", "failed", "cancelled", "interrupted"}
                and not uncertain
            )
        for row in rows:
            clean = row.state == "closed" or await self.close_instance(row, preserve_work=True)
            if clean and discard and row.work_volume:
                rc, raw, _ = await self.runner.cli(
                    "volume", "inspect", "--format", "{{json .Labels}}", row.work_volume
                )
                if rc == 0:
                    labels = json.loads(raw)
                    if (
                        labels.get("tavo.instance") != self.runner.instance
                        or labels.get("tavo.run") != scope.run_id
                    ):
                        raise AppError(Code.POLICY_DENIED)
                    await self.runner.cli("volume", "rm", row.work_volume)
