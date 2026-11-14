import asyncio
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from app.api.schemas import AccountOut, ConnectorOut, DataOut, MockConfig, SyncResult
from app.core.errors import AppError, Code


@dataclass(frozen=True)
class SyncContext:
    account: AccountOut
    revision: int
    cursor: str | None
    attempt: int
    credential: SecretStr
    at: str


class Connector(Protocol):
    info: ConnectorOut

    async def initialize(self) -> None: ...
    async def sync(self, context: SyncContext) -> SyncResult: ...
    async def dispose(self) -> None: ...


class Registry:
    def __init__(self):
        self._items: dict[str, Connector] = {}

    async def register(self, connector: Connector):
        if connector.info.id in self._items:
            raise AppError(Code.INVALID_INPUT)
        await connector.initialize()
        self._items[connector.info.id] = connector

    def get(self, connector_id: str, capability="account"):
        connector = self._items.get(connector_id)
        if not connector:
            raise AppError(Code.NOT_FOUND)
        if (
            capability not in connector.info.capabilities
            or "read:mock" not in connector.info.permissions
        ):
            raise AppError(Code.FORBIDDEN)
        return connector

    def list(self):
        return [item.info for item in self._items.values()]

    async def unregister(self, connector_id):
        connector = self.get(connector_id)
        await connector.dispose()
        del self._items[connector_id]

    async def close(self):
        for connector_id in list(self._items):
            await self.unregister(connector_id)


class MockConnector:
    info = ConnectorOut(
        id="mock",
        name="Mock Connector",
        capabilities=["account", "balance", "usage", "resource", "event"],
        permissions=["read:mock"],
    )

    def __init__(self, latency=0.15):
        self.ready = False
        self.latency = latency

    async def initialize(self):
        self.ready = True

    async def dispose(self):
        self.ready = False

    async def sync(self, context: SyncContext):
        if not self.ready:
            raise AppError(Code.INVALID_STATE)
        if not context.credential.get_secret_value().startswith("mock-only:"):
            raise AppError(Code.AUTH_EXPIRED)
        config = MockConfig.model_validate(context.account.mock)
        await asyncio.sleep(120 if config.scenario == "timeout" else self.latency)
        failures = {
            "auth": Code.AUTH_EXPIRED,
            "forbidden": Code.PERMISSION_DENIED,
            "invalid": Code.INVALID_DATA,
        }
        if config.scenario in failures:
            raise AppError(failures[config.scenario])
        if context.attempt == 1:
            transient = {
                "network_once": Code.NETWORK,
                "rate_limit_once": Code.RATE_LIMITED,
                "server_once": Code.SERVER_ERROR,
            }
            if config.scenario in transient:
                raise AppError(
                    transient[config.scenario],
                    retry_after=1.5 if config.scenario == "rate_limit_once" else 0,
                )
        cursor = f"revision:{context.revision}"
        return SyncResult(
            next_cursor=cursor,
            data=None
            if context.cursor == cursor
            else DataOut(
                connection_id=context.account.id,
                provider_account_id=context.account.external_account_id,
                balance=config.balance,
                currency=config.currency,
                usage_units=config.usage_units,
                resource_status=config.resource_status,
                revision=context.revision,
                updated_at=context.at,
            ),
        )
