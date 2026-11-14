import asyncio

import pytest

from app.connectors.registry import MockConnector, Registry
from app.core.errors import AppError, Code


def test_registry_limits_capabilities_and_owns_lifecycle():
    async def exercise():
        registry = Registry()
        connector = MockConnector(0)
        assert not connector.ready
        await registry.register(connector)
        assert connector.ready and registry.get("mock") is connector
        with pytest.raises(AppError) as duplicate:
            await registry.register(MockConnector(0))
        assert duplicate.value.code == Code.INVALID_INPUT
        with pytest.raises(AppError) as denied:
            registry.get("mock", "write")
        assert denied.value.code == Code.FORBIDDEN
        with pytest.raises(AppError) as missing:
            registry.get("unknown")
        assert missing.value.code == Code.NOT_FOUND
        await registry.close()
        assert not connector.ready and registry.list() == []

    asyncio.run(exercise())
