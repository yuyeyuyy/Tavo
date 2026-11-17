import asyncio
from types import SimpleNamespace

import pytest

from app.agent.pool import AgentPool
from app.core.errors import AppError


def test_pool_bounds_duplicate_ownership_cleanup_capacity_and_close():
    async def exercise():
        external = set()
        pool = AgentPool(2, lambda: external)
        started = []
        gate = asyncio.Event()

        async def execute(scope):
            started.append(scope.run_id)
            await gate.wait()

        a, b = SimpleNamespace(run_id="a"), SimpleNamespace(run_id="b")
        pool.submit(a, execute)
        with pytest.raises(AppError):
            pool.submit(a, execute)
        pool.submit(b, execute)
        with pytest.raises(AppError):
            pool.submit(SimpleNamespace(run_id="c"), execute)
        await asyncio.sleep(0)
        assert started == ["a", "b"]
        external.add("a")
        pool.cancel("a")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert pool.available() == 0
        external.clear()
        assert pool.available() == 1
        await pool.close()
        assert not pool.active and pool.available() == 0

    asyncio.run(exercise())
