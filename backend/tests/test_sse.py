import asyncio
from types import SimpleNamespace

import pytest

from app.api.routes import stream
from app.core.errors import AppError, Code


def test_sse_replays_persistent_sequence_and_ends_expired_sessions(f):
    first = f.add("sse-first")
    second = f.add("sse-second")
    rows = f.client.get("/api/v1/events").json()["items"]
    after = min(row["sequence"] for row in rows)
    token = f.client.headers["Authorization"][7:]

    async def disconnected():
        return False

    async def consume():
        request = SimpleNamespace(is_disconnected=disconnected)
        response = await stream(request, f.services, token, after=0, last_event_id=str(after))
        iterator = response.body_iterator
        frame = await anext(iterator)
        assert f"id: {after + 1}\n" in frame
        assert second["id"] in frame and first["id"] not in frame
        assert "event: business" in frame
        await iterator.aclose()
        resumed = await stream(request, f.services, token, after=after + 1, last_event_id=None)
        assert await anext(resumed.body_iterator) == ": keepalive\n\n"
        f.clock.advance(901)
        expired = await anext(resumed.body_iterator)
        assert "event: session.expired" in expired and "UNAUTHENTICATED" in expired
        with pytest.raises(StopAsyncIteration):
            await anext(resumed.body_iterator)
        for invalid in ("-1", "invalid", "9223372036854775808"):
            with pytest.raises(AppError) as caught:
                await stream(request, f.services, token, after=0, last_event_id=invalid)
            assert caught.value.code == Code.INVALID_INPUT

    asyncio.run(consume())
