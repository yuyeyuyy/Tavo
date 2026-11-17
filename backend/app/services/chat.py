import asyncio

from app.core.errors import AppError, Code
from app.llm.adapter import ModelGateway
from app.llm.contracts import ModelCatalog, ModelOption
from app.llm.keys import ModelKeys


class ChatService:
    def __init__(self, secrets, config, gateway=None, timeout=60, *, retry_policy=None):
        self.keys, self.config = ModelKeys(secrets), config
        self.gateway = gateway or ModelGateway(policy=retry_policy)
        self.timeout, self.active = timeout, 0

    def catalog(self):
        config = self.config.snapshot()
        items = [
            ModelOption(
                id=p.id,
                label=p.label,
                provider=p.provider,
                model=p.model,
                ready=self.keys.ready(p),
                credential_revision=self.keys.revision(p),
            )
            for p in config.profiles
            if p.enabled
        ]
        default = config.default_profile_id
        if not any(p.id == default and p.ready for p in items):
            default = None
        return ModelCatalog(items=items, default_profile_id=default)

    async def complete(self, value, disconnected):
        profile = self.config.profile(value.profile_id)
        if self.active >= 2:
            raise AppError(Code.BUSY)
        credential = self.keys.get(profile)
        self.active += 1
        tasks = []
        try:

            async def watch():
                while not await disconnected():
                    await asyncio.sleep(0.1)
                raise AppError(Code.CANCELLED)

            async with asyncio.timeout(self.timeout):
                upstream = asyncio.create_task(
                    self.gateway.reply(profile, value.messages, credential)
                )
                watcher = asyncio.create_task(watch())
                tasks = [upstream, watcher]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if watcher in done:
                    return await watcher
                return await upstream
        except TimeoutError:
            raise AppError(Code.MODEL_TIMEOUT) from None
        finally:
            for task in tasks:
                task.cancel()
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                self.active -= 1

    async def close(self):
        await self.gateway.close()
