import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from pydantic import ValidationError

from app.agent.tools.definition import ToolContext
from app.core.errors import AppError, Code

OUTPUT_BYTES = 8192
MANAGED_POLICIES = {"3", "4", "5"}


class ToolExecutor:
    def __init__(self, now, *, bash=None, host=None):
        self.now, self.bash, self.host = now, bash, host
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tavo-tools")
        self.readers = {}

    def busy_slots(self):
        return (
            set(self.readers)
            | (self.bash.busy_slots() if self.bash else set())
            | (self.host.busy_slots() if self.host else set())
        )

    @staticmethod
    def route(tool, policy_version):
        if tool.execution == "host":
            if policy_version != "5":
                raise AppError(Code.POLICY_DENIED)
            return "host"
        if tool.execution == "sandbox":
            if policy_version not in MANAGED_POLICIES:
                raise AppError(Code.POLICY_DENIED)
            return "sandbox"
        return "local"

    @staticmethod
    def output(tool, result):
        try:
            payload = tool.result.model_validate(result).model_dump(mode="json", by_alias=True)
            size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
        except (ValidationError, ValueError):
            raise AppError(Code.INVALID_DATA) from None
        if size > OUTPUT_BYTES:
            raise AppError(Code.CONTEXT_TOO_LARGE)
        return payload

    async def local(self, scope, tool, args, remaining, *, web_policy=None):
        timeout = min(tool.timeout_seconds, remaining)
        if timeout <= 0:
            raise AppError(Code.TOOL_BUDGET_EXCEEDED)
        stopped = Event()
        context = ToolContext(self.now, web_policy=web_policy, cancelled=stopped.is_set)
        try:
            if tool.execution == "async":
                async with asyncio.timeout(timeout):
                    result = await tool.execute(args, context)
            else:
                result = await self._sync(scope.run_id, tool, args, context, timeout)
            return self.output(tool, result)
        except TimeoutError:
            raise AppError(Code.TIMEOUT) from None
        finally:
            stopped.set()

    async def _sync(self, run_id, tool, args, context, timeout):
        if run_id in self.readers or len(self.readers) >= 2:
            raise AppError(Code.TOOL_BUDGET_EXCEEDED)
        future = asyncio.get_running_loop().run_in_executor(self.pool, tool.execute, args, context)
        self.readers[run_id] = future

        def release(completed):
            if self.readers.get(run_id) is completed:
                self.readers.pop(run_id, None)
            if not completed.cancelled():
                completed.exception()

        future.add_done_callback(release)
        done, _ = await asyncio.wait({future}, timeout=timeout)
        if not done:
            raise AppError(Code.TIMEOUT)
        return future.result()

    async def command(self, route, scope, call_id, tool, args, *, resume=False):
        if route == "host":
            if not self.host:
                raise AppError(Code.HOST_UNAVAILABLE)
            result, error = await self.host.execute(scope, call_id, args)
        elif not self.bash or not self.bash.settings.bash_enabled:
            raise AppError(Code.BASH_UNAVAILABLE)
        elif resume:
            payload, error = await self.bash.manager.resume_commit(scope, call_id)
            return self.output(tool, payload), error
        elif tool.spec.name == "http_request":
            result, error = await self.bash.manager.egress.execute(scope, call_id, args.request_id)
        else:
            kind = "bash" if tool.spec.name == "sandbox" else tool.spec.name
            result, error = await self.bash.manager.execute(scope, call_id, args, kind=kind)
        return self.output(tool, result), error

    async def close(self):
        await asyncio.to_thread(self.pool.shutdown, wait=True)
