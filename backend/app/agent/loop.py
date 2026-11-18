import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

from app.agent.contracts import RunScope
from app.agent.graph import STEPS, LoopExit, RunSteps, StepName, create_graph
from app.core.errors import AppError, Code
from app.repositories.database import Database
from app.repositories.sessions import SessionRepository, require_scope


class AgentLoop:
    """Drive one worker's claim of a Run from its committed step."""

    def __init__(self, db: Database, repository: SessionRepository, now: Callable[[], str]):
        self.db, self.repository, self.now = db, repository, now

    async def run(
        self,
        scope: RunScope,
        steps: RunSteps,
        *,
        recursion_limit: int,
        cleanup: Callable[[RunScope], Awaitable[None]] | None = None,
    ) -> LoopExit | None:
        try:
            with self.db.session() as db:
                step = require_scope(db, scope, self.now()).step
            if step not in STEPS:
                raise AppError(Code.INVALID_STATE)
            result = await create_graph(steps, scope).ainvoke(
                {"entry_step": cast(StepName, step)}, {"recursion_limit": recursion_limit}
            )
            outcome = result.get("outcome")
            if not isinstance(outcome, LoopExit):
                raise AppError(Code.INVALID_STATE)
        except asyncio.CancelledError:
            self._finish_failed(scope, Code.INTERRUPTED)
            raise
        except AppError as error:
            self._finish_failed(scope, error.code)
        except Exception:
            self._finish_failed(scope, Code.INTERNAL)
        else:
            return outcome
        finally:
            if cleanup is not None:
                await cleanup(scope)
        return None

    def _finish_failed(self, scope: RunScope, code: Code) -> None:
        status = (
            "interrupted" if code in {Code.INTERRUPTED, Code.TOOL_EXECUTION_UNKNOWN} else "failed"
        )
        try:
            with self.db.session(write=True) as db:
                run = require_scope(db, scope, self.now())
                self.repository.finish(db, run, status, code.value)
        except AppError as error:
            if error.code not in {Code.LEASE_LOST, Code.CANCELLED, Code.NOT_FOUND}:
                raise
