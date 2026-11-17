import asyncio

from app.core.errors import AppError, Code


class AgentPool:
    """Bounded Run ownership; durable queue and lease remain in Scheduler/Repository."""

    def __init__(self, capacity, external_slots):
        if not 1 <= capacity <= 2:
            raise ValueError("Run capacity must be 1..2")
        self.capacity = capacity
        self.external_slots = external_slots
        self.active = {}
        self.closing = False
        self.last_error = None

    def occupied(self):
        return set(self.active) | self.external_slots()

    def available(self):
        return max(0, self.capacity - len(self.occupied())) if not self.closing else 0

    def submit(self, scope, execute):
        if not self.available() or scope.run_id in self.occupied():
            raise AppError(Code.BUSY)
        task = asyncio.create_task(execute(scope))
        self.active[scope.run_id] = task

        def finished(completed):
            if self.active.get(scope.run_id) is completed:
                self.active.pop(scope.run_id, None)
            if not completed.cancelled():
                error = completed.exception()
                if error:
                    self.last_error = error.code if isinstance(error, AppError) else Code.INTERNAL

        task.add_done_callback(finished)

    def cancel(self, run_id):
        task = self.active.get(run_id)
        if task:
            task.cancel()

    async def close(self):
        self.closing = True
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
