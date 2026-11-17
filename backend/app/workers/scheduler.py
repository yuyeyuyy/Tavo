import asyncio
from uuid import uuid4

from sqlalchemy import select

from app.agent.contracts import RunScope
from app.agent.pool import AgentPool
from app.core.errors import AppError, Code
from app.models.entities import (
    AssistantModelTurn,
    AssistantRun,
    AssistantToolCall,
    AssistantToolResult,
    BashExecution,
    ConversationSession,
    HostExecution,
    SandboxNetworkRequest,
    SandboxOperation,
    WorkerLease,
)
from app.repositories.sessions import require_leader
from app.repositories.time import plus_seconds


class Scheduler:
    """Single leader durable AssistantRun worker; historical account queues are never read."""

    def __init__(self, services):
        self.s = services
        self.owner = str(uuid4())
        self.fence = 0
        self.pool = AgentPool(services.settings.concurrency, services.assistant.busy_slots)
        self.active = self.pool.active
        self.stopped = asyncio.Event()
        self.runner = None
        self.last_error = None

    def lead(self):
        now = self.s.now()
        with self.s.db.session(write=True) as db:
            lease = db.get(WorkerLease, 1)
            if lease.owner == self.owner and lease.fence == self.fence and lease.expires_at > now:
                lease.expires_at = plus_seconds(now, self.s.settings.lease_seconds)
                return True
            if lease.expires_at > now:
                return False
            lease.owner = self.owner
            lease.fence += 1
            lease.expires_at = plus_seconds(now, self.s.settings.lease_seconds)
            self.fence = lease.fence
            for run in db.scalars(select(AssistantRun).where(AssistantRun.status == "running")):
                unknown = db.scalar(
                    select(AssistantModelTurn).where(
                        AssistantModelTurn.run_id == run.id, AssistantModelTurn.status == "inflight"
                    )
                )
                bash = db.scalar(
                    select(BashExecution)
                    .join(AssistantToolCall, BashExecution.tool_call_id == AssistantToolCall.id)
                    .where(
                        AssistantToolCall.run_id == run.id,
                        ~select(AssistantToolResult.id)
                        .where(AssistantToolResult.tool_call_id == AssistantToolCall.id)
                        .exists(),
                    )
                )
                managed = db.scalar(
                    select(SandboxOperation.tool_call_id)
                    .join(AssistantToolCall, SandboxOperation.tool_call_id == AssistantToolCall.id)
                    .where(
                        AssistantToolCall.run_id == run.id,
                        SandboxOperation.state.in_(("running", "committing")),
                    )
                )
                network = db.scalar(
                    select(SandboxNetworkRequest).where(
                        SandboxNetworkRequest.run_id == run.id,
                        SandboxNetworkRequest.state == "inflight",
                    )
                )
                if network:
                    network.state = "unknown"
                host = db.scalar(
                    select(HostExecution)
                    .join(AssistantToolCall, HostExecution.tool_call_id == AssistantToolCall.id)
                    .where(
                        AssistantToolCall.run_id == run.id,
                        ~select(AssistantToolResult.id)
                        .where(AssistantToolResult.tool_call_id == AssistantToolCall.id)
                        .exists(),
                    )
                )
                bash = bash or managed or network or host
                if bash:
                    db.get(ConversationSession, run.session_id).workspace_unconfirmed = True
                    self.s.conversations.finish(db, run, "interrupted", "TOOL_EXECUTION_UNKNOWN")
                elif unknown:
                    unknown.status = "unknown"
                    unknown.finished_at = now
                    self.s.conversations.finish(db, run, "interrupted", "INTERRUPTED")
                else:
                    run.status = "pending"
                    run.revision += 1
            return True

    async def tick(self):
        if self.stopped.is_set():
            return
        if not self.lead():
            for task in self.active.values():
                task.cancel()
            return
        await self.s.bash.reconcile(self.owner, self.fence)
        await self.s.host.reconcile(self.owner, self.fence)
        with self.s.db.session(write=True) as db:
            require_leader(db, self.owner, self.fence, self.s.now())
            for key, task in self.active.items():
                row = db.get(AssistantRun, key)
                if not row or row.status in ("cancelled", "failed", "interrupted"):
                    task.cancel()
            expired = list(
                db.scalars(
                    select(AssistantToolCall).where(
                        AssistantToolCall.status == "waiting_review",
                        AssistantToolCall.expires_at <= self.s.now(),
                    )
                )
            )
            for call in expired:
                run = db.get(AssistantRun, call.run_id)
                if run.status == "waiting_review":
                    self.s.conversations.review(db, call, "deny", "policy", "REVIEW_EXPIRED")
                    self.s.conversations.result(db, run, call, "denied", {}, "REVIEW_EXPIRED")
                    self.s.conversations.finish(db, run, "failed", "REVIEW_EXPIRED")
            pending = list(
                db.scalars(
                    select(AssistantRun)
                    .where(AssistantRun.status == "pending")
                    .where(AssistantRun.id.not_in(self.pool.occupied()))
                    .order_by(AssistantRun.created_at, AssistantRun.id)
                    .limit(self.pool.available())
                )
            )
            ready = []
            slots = set(self.active) | self.s.assistant.busy_slots()
            for row in pending:
                if len(slots) >= self.s.settings.concurrency:
                    break
                if row.id in slots:
                    continue
                session = db.get(ConversationSession, row.session_id)
                try:
                    if row.profile is None and session.profile_id != "mock":
                        row.profile = self.s.models.profile(session.profile_id).model_dump()
                except AppError as error:
                    self.s.conversations.finish(db, row, "failed", error.code.value)
                    continue
                row.status = "running"
                row.worker_owner = self.owner
                row.worker_fence = self.fence
                row.started_at = row.started_at or self.s.now()
                row.revision += 1
                ready.append(RunScope(row.id, row.session_id, self.owner, self.fence))
                slots.add(row.id)
        for scope in ready:
            self.pool.submit(scope, self.s.assistant.run)

    def start(self):
        async def loop():
            while not self.stopped.is_set():
                try:
                    await self.tick()
                except AppError as error:
                    self.last_error = error.code
                except Exception:
                    self.last_error = Code.INTERNAL
                try:
                    await asyncio.wait_for(self.stopped.wait(), self.s.settings.poll_seconds)
                except TimeoutError:
                    pass

        self.runner = asyncio.create_task(loop())

    async def close(self):
        self.stopped.set()
        if self.runner:
            await self.runner
        await self.pool.close()
        await self.s.bash.reconcile(self.owner, self.fence, only_owned=True)
        with self.s.db.session(write=True) as db:
            lease = db.get(WorkerLease, 1)
            if lease and lease.owner == self.owner and lease.fence == self.fence:
                lease.owner = ""
                lease.expires_at = self.s.now()
