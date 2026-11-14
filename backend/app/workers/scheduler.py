import asyncio
import random
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.api.schemas import AccountOut, Analysis, SyncResult
from app.connectors.registry import SyncContext
from app.core.errors import RETRYABLE, AppError, Code
from app.models.entities import (
    Account,
    AgentRun,
    BusinessEvent,
    LegacyRecord,
    SyncTask,
    WorkerLease,
)
from app.repositories.time import plus_seconds
from app.repositories.workspace import commit_sync, preferences, require_leader


def retry_delay(attempt, random_source=random.random, retry_after=0):
    return max(
        min(30, 2 ** max(0, attempt - 1)) * (0.5 + min(1, max(0, random_source())) * 0.5),
        min(86400, max(0, retry_after)),
    )


@dataclass
class Work:
    task: asyncio.Task
    kind: str
    account_id: str


class Scheduler:
    """Durable single-leader scheduler. API processes may compete for the same lease."""

    def __init__(self, services, random_source=random.random):
        self.s = services
        self.owner = str(uuid4())
        self.fence = 0
        self.random = random_source
        self.active: dict[str, Work] = {}
        self.requests: dict[str, asyncio.Task] = {}
        self.stopped = asyncio.Event()
        self.runner: asyncio.Task | None = None
        self.last_error: Code | None = None

    def lead(self):
        now = self.s.now()
        with self.s.db.session(write=True) as session:
            lease = session.get(WorkerLease, 1)
            if lease.owner == self.owner and lease.fence == self.fence and lease.expires_at > now:
                lease.expires_at = plus_seconds(now, self.s.settings.lease_seconds)
                return True
            if lease.expires_at > now:
                return False
            lease.owner, lease.fence, lease.expires_at = (
                self.owner,
                lease.fence + 1,
                plus_seconds(now, self.s.settings.lease_seconds),
            )
            self.fence = lease.fence
            # The new leader fences old commits before recovering their running tasks.
            for job in session.scalars(select(SyncTask).where(SyncTask.status == "running")):
                account = session.get(Account, job.connection_id)
                if (
                    not account
                    or account.deleted_at
                    or not account.credential_configured
                    or account.status not in {"connected", "error"}
                ):
                    job.status, job.error_code, job.finished_at = "cancelled", Code.CANCELLED, now
                else:
                    retry = job.attempts < job.max_attempts
                    job.status, job.error_code = (
                        ("pending" if retry else "failed"),
                        Code.INTERRUPTED,
                    )
                    job.next_attempt_at, job.finished_at = now, None if retry else now
                    self.s.event_repository.append(
                        session,
                        account,
                        "sync.retry_scheduled" if retry else "sync.failed",
                        job.id,
                        now,
                        {
                            "errorCode": Code.INTERRUPTED,
                            "attempt": job.attempts,
                            **({"nextAttemptAt": now} if retry else {}),
                        },
                        f"{job.id}:recover:{self.fence}",
                    )
            for run in session.scalars(select(AgentRun).where(AgentRun.status == "running")):
                run.status, run.progress, run.error_code = "pending", 0, Code.INTERRUPTED
            return True

    def slots(self):
        return {
            w.account_id if w.kind == "sync" else f"agent:{key}" for key, w in self.active.items()
        } | set(self.requests)

    def _finished(self, key, task):
        self.active.pop(key, None)
        if not task.cancelled():
            error = task.exception()
            if error:
                self.last_error = error.code if isinstance(error, AppError) else Code.INTERNAL

    async def tick(self):
        if self.stopped.is_set():
            return
        if not self.lead():
            for work in self.active.values():
                work.task.cancel()
            return
        with self.s.db.session(write=True) as session:
            require_leader(session, self.owner, self.fence, self.s.now())
            for key, work in self.active.items():
                row = session.get(SyncTask if work.kind == "sync" else AgentRun, key)
                if not row or row.status in {"cancelled", "failed"}:
                    work.task.cancel()
            config = preferences(session)
            if config.auto_sync:
                for account in session.scalars(
                    select(Account).where(
                        Account.deleted_at.is_(None),
                        Account.credential_configured.is_(True),
                        Account.next_sync_at <= self.s.now(),
                        Account.status.in_(("connected", "error")),
                    )
                ):
                    if account.id in self.requests or (
                        account.error_code and account.error_code not in RETRYABLE
                    ):
                        continue
                    try:
                        self.s.accounts.enqueue_in(session, account.id, "scheduled")
                    except AppError as error:
                        if error.code != Code.BUSY:
                            raise
            pending = [
                (row.id, "sync", row.connection_id, row.created_at)
                for row in session.scalars(
                    select(SyncTask)
                    .where(SyncTask.status == "pending", SyncTask.next_attempt_at <= self.s.now())
                    .order_by(SyncTask.created_at, SyncTask.id)
                )
            ]
            pending += [
                (row.id, "agent", row.account_id, row.created_at)
                for row in session.scalars(select(AgentRun).where(AgentRun.status == "pending"))
            ]
        for key, kind, account_id, _created in sorted(pending, key=lambda row: (row[3], row[0])):
            if len(self.slots()) >= self.s.settings.concurrency:
                break
            if key in self.active or (kind == "sync" and account_id in self.slots()):
                continue
            task = asyncio.create_task(
                self.execute_sync(key) if kind == "sync" else self.execute_agent(key)
            )
            self.active[key] = Work(task, kind, account_id)
            task.add_done_callback(
                lambda completed, task_id=key: self._finished(task_id, completed)
            )
        await asyncio.sleep(0)

    async def drain(self):
        tasks = [w.task for w in self.active.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
        if self.last_error:
            code, self.last_error = self.last_error, None
            raise AppError(code)

    def fail_sync(self, task_id, code, retry_after=0):
        with self.s.db.session(write=True) as session:
            require_leader(session, self.owner, self.fence, self.s.now())
            job = session.get(SyncTask, task_id)
            if (
                not job
                or job.status != "running"
                or job.worker_owner != self.owner
                or job.worker_fence != self.fence
            ):
                return
            account = session.get(Account, job.connection_id)
            if code == Code.CANCELLED or account.deleted_at or not account.credential_configured:
                job.status, job.error_code, job.finished_at = (
                    "cancelled",
                    Code.CANCELLED,
                    self.s.now(),
                )
                self.s.event_repository.append(
                    session,
                    account,
                    "sync.cancelled",
                    job.id,
                    self.s.now(),
                    {"errorCode": Code.CANCELLED},
                )
                return
            retry = code in RETRYABLE and job.attempts < job.max_attempts
            job.status, job.error_code = ("pending" if retry else "failed"), code
            job.finished_at = None if retry else self.s.now()
            if retry:
                job.next_attempt_at = plus_seconds(
                    self.s.now(), retry_delay(job.attempts, self.random, retry_after)
                )
            account.status, account.error_code = (
                ("expired" if code == Code.AUTH_EXPIRED else "error"),
                code,
            )
            account.next_sync_at = plus_seconds(
                self.s.now(), preferences(session).sync_interval_seconds
            )
            self.s.event_repository.append(
                session,
                account,
                "sync.retry_scheduled" if retry else "sync.failed",
                job.id,
                self.s.now(),
                {
                    "errorCode": code,
                    "attempt": job.attempts,
                    **({"nextAttemptAt": job.next_attempt_at} if retry else {}),
                },
                f"{job.id}:failure:{job.attempts}",
            )

    async def execute_sync(self, task_id):
        request = None
        try:
            with self.s.db.session(write=True) as session:
                require_leader(session, self.owner, self.fence, self.s.now())
                job = session.get(SyncTask, task_id)
                if not job or job.status != "pending":
                    return
                account = session.get(Account, job.connection_id)
                if (
                    account.deleted_at
                    or not account.credential_configured
                    or account.status not in {"connected", "error"}
                ):
                    job.status, job.error_code, job.finished_at = (
                        "cancelled",
                        Code.CANCELLED,
                        self.s.now(),
                    )
                    return
                job.status, job.attempts = "running", job.attempts + 1
                job.retry_count, job.started_at = job.attempts - 1, job.started_at or self.s.now()
                job.worker_owner, job.worker_fence = self.owner, self.fence
                self.s.event_repository.append(
                    session,
                    account,
                    "sync.started",
                    job.id,
                    self.s.now(),
                    {"attempt": job.attempts},
                    f"{job.id}:start:{job.attempts}",
                )
                context = dict(
                    account=AccountOut.model_validate(account),
                    revision=account.revision,
                    cursor=account.cursor,
                    attempt=job.attempts,
                    at=self.s.now(),
                )
            connector = self.s.registry.get(context["account"].connector_id)
            credential = self.s.secrets.for_connector(context["account"].id, connector.info.id)
            request = asyncio.create_task(
                connector.sync(SyncContext(**context, credential=credential))
            )
            account_id = context["account"].id
            self.requests[account_id] = request

            def release(completed):
                if self.requests.get(account_id) is completed:
                    self.requests.pop(account_id, None)
                if not completed.cancelled():
                    completed.exception()  # consume rejected late requests without logging raw errors

            request.add_done_callback(release)
            done, _ = await asyncio.wait({request}, timeout=self.s.settings.timeout_seconds)
            if not done:
                request.cancel()
                raise AppError(Code.TIMEOUT)
            result = SyncResult.model_validate(await request)
            if credential.get_secret_value() in result.model_dump_json():
                raise AppError(Code.INVALID_DATA)
            with self.s.db.session(write=True) as session:
                commit_sync(
                    session,
                    self.s.event_repository,
                    session.get(SyncTask, task_id),
                    result,
                    self.owner,
                    self.fence,
                    self.s.now(),
                )
        except BaseException as error:
            if request and not request.done():
                request.cancel()
            code = (
                Code.CANCELLED
                if isinstance(error, asyncio.CancelledError)
                else error.code
                if isinstance(error, AppError)
                else Code.INVALID_DATA
                if isinstance(error, ValidationError)
                else Code.INTERNAL
            )
            if code != Code.LEASE_LOST:
                try:
                    self.fail_sync(
                        task_id, code, error.retry_after if isinstance(error, AppError) else 0
                    )
                except AppError as failure:
                    if failure.code != Code.LEASE_LOST:
                        self.last_error = failure.code

    async def execute_agent(self, run_id):
        try:
            with self.s.db.session(write=True) as session:
                require_leader(session, self.owner, self.fence, self.s.now())
                run = session.get(AgentRun, run_id)
                if not run or run.status != "pending":
                    return
                account_id, event = self.s.agent.input_in(session, run.event_id)
                run.status, run.progress, run.started_at = "running", 10, self.s.now()
                run.worker_owner, run.worker_fence = self.owner, self.fence
                self.s.event_repository.append(
                    session,
                    session.get(Account, account_id),
                    "agent.started",
                    run.id,
                    self.s.now(),
                    {"runId": run.id},
                )
            analysis = None
            async for update in self.s.graph.astream(
                {"event": event.model_dump()}, stream_mode="updates"
            ):
                stage = next(iter(update))
                if "analysis" in update[stage]:
                    analysis = Analysis.model_validate(update[stage]["analysis"])
                with self.s.db.session(write=True) as session:
                    require_leader(session, self.owner, self.fence, self.s.now())
                    run = session.get(AgentRun, run_id)
                    if run.status != "running" or run.worker_fence != self.fence:
                        raise AppError(Code.CANCELLED)
                    run.progress = 50 if stage == "classify" else 90
                    self.s.event_repository.append(
                        session,
                        session.get(Account, account_id),
                        "agent.progress",
                        run.id,
                        self.s.now(),
                        {"runId": run.id, "stage": stage, "progress": run.progress},
                        f"{run.id}:{stage}",
                    )
            if not analysis or analysis.event_id != event.id:
                raise AppError(Code.INVALID_DATA)
            with self.s.db.session(write=True) as session:
                require_leader(session, self.owner, self.fence, self.s.now())
                run = session.get(AgentRun, run_id)
                if (
                    run.status != "running"
                    or run.worker_owner != self.owner
                    or run.worker_fence != self.fence
                ):
                    raise AppError(Code.CANCELLED)
                result = analysis.model_dump(by_alias=True)
                source = session.scalar(
                    select(BusinessEvent).where(BusinessEvent.event_id == run.event_id)
                )
                if source:
                    source.analysis = result
                else:
                    legacy = session.scalar(
                        select(LegacyRecord).where(
                            LegacyRecord.table_name == "events",
                            LegacyRecord.record_id == run.event_id,
                        )
                    )
                    legacy.data = {**legacy.data, "analysis": result}
                run.result, run.status, run.progress, run.finished_at, run.error_code = (
                    result,
                    "succeeded",
                    100,
                    self.s.now(),
                    None,
                )
                self.s.event_repository.append(
                    session,
                    session.get(Account, account_id),
                    "agent.completed",
                    run.id,
                    self.s.now(),
                    {"runId": run.id, "result": result},
                )
        except BaseException as error:
            code = (
                Code.CANCELLED
                if isinstance(error, asyncio.CancelledError)
                else error.code
                if isinstance(error, AppError)
                else Code.INTERNAL
            )
            if code == Code.LEASE_LOST:
                return
            try:
                with self.s.db.session(write=True) as session:
                    require_leader(session, self.owner, self.fence, self.s.now())
                    run = session.get(AgentRun, run_id)
                    if (
                        run
                        and run.status == "running"
                        and run.worker_owner == self.owner
                        and run.worker_fence == self.fence
                    ):
                        run.status, run.error_code, run.finished_at = (
                            ("cancelled" if code == Code.CANCELLED else "failed"),
                            code,
                            self.s.now(),
                        )
                        self.s.event_repository.append(
                            session,
                            session.get(Account, run.account_id),
                            "agent.failed",
                            run.id,
                            self.s.now(),
                            {"runId": run.id, "errorCode": code},
                        )
            except AppError as failure:
                if failure.code != Code.LEASE_LOST:
                    self.last_error = failure.code

    async def run(self):
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

    def start(self):
        if not self.runner:
            self.runner = asyncio.create_task(self.run())

    async def close(self):
        self.stopped.set()
        if self.runner:
            await self.runner
        for work in list(self.active.values()):
            work.task.cancel()
        await self.drain()
        for request in self.requests.values():
            request.cancel()
        with self.s.db.session(write=True) as session:
            lease = session.get(WorkerLease, 1)
            if lease and lease.owner == self.owner and lease.fence == self.fence:
                lease.owner, lease.expires_at = "", self.s.now()
