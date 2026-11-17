"""Fixed, credential-free terminal progress, independent of application log levels."""

import asyncio
import sys
import time
from contextlib import asynccontextmanager, suppress

PROGRESS_INTERVAL = 5


def report_startup(message: str):
    print(f"[Tavo 启动] {message}", file=sys.stderr, flush=True)


def audit_tool(stage: str, outcome: str, call_id: str):
    import json

    print(
        json.dumps(
            {"event": "tool_audit", "stage": stage, "outcome": str(outcome), "toolCallId": call_id},
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


@asynccontextmanager
async def startup_stage(message: str):
    report_startup(message)
    started = time.monotonic()

    async def waiting():
        while True:
            await asyncio.sleep(PROGRESS_INTERVAL)
            report_startup(f"{message}（已等待 {int(time.monotonic() - started)} 秒）")

    heartbeat = asyncio.create_task(waiting())
    try:
        yield
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
