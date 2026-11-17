import asyncio
import json
import logging
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.errors import AppError, Code
from app.llm.adapter import ModelGateway, request_for
from app.llm.contracts import ChatMessage
from app.llm.retry import ModelRetryPolicy, response_metadata, retry_after
from app.llm.turns import parse_turn, turn_request
from app.models.entities import AssistantModelTurn, ConversationMessage
from tests.test_agent_protocols import configure, tool_response
from tests.test_models import KEY, PRIVATE, body, profile
from tests.test_sessions import drive, get_run, send, session


@pytest.fixture
def anyio_backend():
    return "asyncio"


def empty_reply(*, limited=True, refusal=False):
    return {
        "choices": [
            {
                "finish_reason": "length" if limited else "stop",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": PRIVATE,
                    **({"refusal": PRIVATE} if refusal else {}),
                },
            }
        ],
        "usage": {
            "completion_tokens": 2048,
            "completion_tokens_details": {"reasoning_tokens": 2048},
        },
    }


def gateway(handler, **kwargs):
    waits, requests = [], []

    async def sleep(delay):
        waits.append(delay)

    def respond(request):
        requests.append(json.loads(request.content))
        return handler(len(requests))

    value = ModelGateway(httpx.MockTransport(respond), sleep=sleep, **kwargs)
    return value, requests, waits


async def invoke(g, p=None, kind="turn"):
    p = p or profile().model_copy(update={"model": "deepseek-flash"})
    if kind == "reply":
        return await g.reply(p, [ChatMessage(role="user", content="synthetic")], SecretStr(KEY))
    return await g.turn(
        p,
        [{"role": "user", "content": "synthetic"}],
        [],
        SecretStr(KEY),
        **({"system": "synthetic reviewer"} if kind == "review" else {}),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["reply", "turn", "review"])
async def test_empty_reasoning_grows_budget_then_succeeds(kind, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error.tavo.model")
    g, requests, waits = gateway(
        lambda n: httpx.Response(200, json=empty_reply() if n < 4 else body("completions"))
    )
    try:
        await invoke(g, kind=kind)
        assert [r["max_tokens"] for r in requests] == [2048, 4096, 8192, 16384]
        assert all(r["thinking"] == {"type": "enabled"} for r in requests)
        assert all(r["messages"] == requests[0]["messages"] for r in requests)
        assert waits == [0.25, 0.5, 1.0]
        events = [
            json.loads(r.message.split("model_request ")[1])
            for r in caplog.records
            if r.message.startswith("model_request ")
        ]
        assert [e["outcome"] for e in events] == ["retry", "retry", "retry", "success"]
        assert len({e["call"] for e in events}) == 1
        assert events[0]["reasoning_tokens"] == 2048 and events[0]["stage"] == "parse"
        assert (
            KEY not in caplog.text
            and PRIVATE not in caplog.text
            and "synthetic reviewer" not in caplog.text
        )
    finally:
        await g.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "retries,cap,expected",
    [
        (3, 16384, [2048, 4096, 8192, 16384]),
        (0, 16384, [2048]),
        (3, 4096, [2048, 4096]),
        (3, 128, [2048]),
    ],
)
async def test_output_limit_stops_at_attempt_or_budget_cap(retries, cap, expected):
    g, requests, _ = gateway(
        lambda _: httpx.Response(200, json=empty_reply()),
        policy=ModelRetryPolicy(max_retries=retries, max_output_tokens=cap),
    )
    try:
        with pytest.raises(AppError) as error:
            await invoke(g)
        assert error.value.code == Code.MODEL_OUTPUT_LIMIT
        assert [r["max_tokens"] for r in requests] == expected
    finally:
        await g.close()


@pytest.mark.anyio
async def test_blank_stop_retries_but_clear_refusal_does_not():
    for refusal, attempts, code in [
        (False, 4, Code.MODEL_EMPTY_RESPONSE),
        (True, 1, Code.MODEL_REJECTED),
    ]:
        g, requests, _ = gateway(
            lambda _: httpx.Response(200, json=empty_reply(limited=False, refusal=refusal))
        )
        try:
            with pytest.raises(AppError) as error:
                await invoke(g)
            assert error.value.code == code and len(requests) == attempts
            assert {r["max_tokens"] for r in requests} == {2048}
        finally:
            await g.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,attempts",
    [
        (400, 1),
        (401, 1),
        (402, 1),
        (403, 1),
        (404, 1),
        (429, 4),
        (500, 4),
        (502, 4),
        (503, 4),
        (504, 4),
        (501, 1),
        (302, 1),
    ],
)
async def test_http_retry_classification(status, attempts):
    g, requests, _ = gateway(lambda _: httpx.Response(status, json={"error": {"message": PRIVATE}}))
    try:
        with pytest.raises(AppError):
            await invoke(g)
        assert len(requests) == attempts
    finally:
        await g.close()


@pytest.mark.anyio
@pytest.mark.parametrize("hint,quota,attempts", [("2", False, 4), ("10", False, 1), ("2", True, 1)])
async def test_retry_after_and_explicit_quota(hint, quota, attempts):
    g, requests, waits = gateway(
        lambda _: httpx.Response(
            429,
            headers={"Retry-After": hint},
            json={
                "error": {
                    "code": "insufficient_quota" if quota else "rate_limit",
                    "message": PRIVATE,
                }
            },
        )
    )
    try:
        with pytest.raises(AppError) as error:
            await invoke(g)
        assert error.value.code == Code.MODEL_RATE_LIMITED and len(requests) == attempts
        assert waits == ([2, 2, 2] if attempts == 4 else [])
    finally:
        await g.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider", ["openai", "anthropic", "gemini", "qwen", "kimi", "zhipu", "openrouter", "xai"]
)
async def test_other_providers_do_not_get_deepseek_parameters(provider):
    p = profile(provider)
    g, requests, _ = gateway(
        lambda n: httpx.Response(503) if n == 1 else httpx.Response(200, json=body(p.protocol))
    )
    try:
        await invoke(g, p)
        assert len(requests) == 2 and requests[0] == requests[1]
        assert "thinking" not in requests[0] and "reasoning_effort" not in requests[0]
    finally:
        await g.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    ["truncated_text", "tool_call", "truncated_tool", "key_echo", "invalid_json", "filtered"],
)
async def test_success_and_unsafe_responses_are_not_replayed(case):
    raw = body("completions")
    if case == "truncated_text":
        raw["choices"][0]["finish_reason"] = "length"
    elif case in {"tool_call", "truncated_tool"}:
        raw = tool_response("completions")
        if case == "truncated_tool":
            raw["choices"][0]["finish_reason"] = "length"
    elif case == "key_echo":
        raw["choices"][0]["message"]["reasoning_content"] = KEY
    elif case == "filtered":
        raw["choices"][0]["finish_reason"] = "content_filter"
    g, requests, _ = gateway(
        lambda _: (
            httpx.Response(200, json=raw)
            if case != "invalid_json"
            else httpx.Response(200, content=b"{", headers={"Content-Type": "application/json"})
        )
    )
    try:
        if case in {"truncated_text", "tool_call"}:
            value = await invoke(g)
            assert value.truncated if case == "truncated_text" else len(value.calls) == 1
        else:
            with pytest.raises(AppError):
                await invoke(g)
        assert len(requests) == 1
    finally:
        await g.close()


@pytest.mark.anyio
async def test_transport_timeout_can_recover_but_logical_deadline_is_bounded():
    attempts = 0

    async def respond(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout(PRIVATE)
        return httpx.Response(200, json=body("completions"))

    async def sleep(_delay):
        pass

    g = ModelGateway(httpx.MockTransport(respond), sleep=sleep)
    try:
        await invoke(g)
        assert attempts == 2
    finally:
        await g.close()

    async def blocked(_request):
        await asyncio.Event().wait()

    g = ModelGateway(httpx.MockTransport(blocked), policy=ModelRetryPolicy(timeout_seconds=0.02))
    try:
        with pytest.raises(AppError) as error:
            await invoke(g)
        assert error.value.code == Code.MODEL_TIMEOUT
    finally:
        await g.close()


@pytest.mark.anyio
async def test_cancel_during_retry_wait_stops_further_requests():
    waiting = asyncio.Event()

    async def sleep(_delay):
        waiting.set()
        await asyncio.Event().wait()

    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503)

    g = ModelGateway(httpx.MockTransport(respond), sleep=sleep)
    try:
        task = asyncio.create_task(invoke(g))
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1
    finally:
        await g.close()


def test_configuration_and_untrusted_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVO_MODEL_MAX_RETRIES", "0")
    assert Settings.from_env().model_retry.max_retries == 0
    monkeypatch.setenv("TAVO_MODEL_MAX_RETRIES", "4")
    with pytest.raises(ValueError, match="Invalid model retry configuration"):
        Settings.from_env()
    for kwargs in (
        {"max_retries": -1},
        {"max_retries": True},
        {"max_output_tokens": 16385},
        {"base_delay_seconds": float("nan")},
    ):
        with pytest.raises(ValidationError):
            ModelRetryPolicy(**kwargs)
    now = datetime(2026, 11, 17, tzinfo=UTC).timestamp()
    assert retry_after(format_datetime(datetime.fromtimestamp(now + 3, UTC)), now) == 3
    assert retry_after("NaN", now) is None and retry_after(PRIVATE, now) is None
    assert retry_after("-2", now) == 0
    assert (
        response_metadata(
            {"choices": [{"finish_reason": PRIVATE, "message": {"content": PRIVATE}}]},
            "completions",
        )["finish_reason"]
        == "other"
    )
    p = profile("qwen")
    with pytest.raises(ValueError):
        request_for(p, [], KEY, output_budget=16384)
    with pytest.raises(ValueError):
        turn_request(p, [], [], output_budget=True)


def test_agent_retry_does_not_repeat_tools_or_user_messages(f):
    requests = []

    def respond(request):
        value = json.loads(request.content)
        requests.append(value)
        if len(requests) in {1, 3}:
            return httpx.Response(503)
        return httpx.Response(200, json=tool_response("completions", final=len(requests) == 4))

    p = configure(f, "deepseek", respond)
    s = session(f, p.id, mode="auto_readonly")
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded" and len(run["toolCalls"]) == 1
    assert run["toolCalls"][0]["attempts"] == 1
    assert len(requests) == 4 and requests[0] == requests[1] and requests[2] == requests[3]
    with f.services.db.session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ConversationMessage)
                .where(ConversationMessage.kind == "user")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AssistantModelTurn)
                .where(AssistantModelTurn.purpose == "assistant")
            )
            == 2
        )


def test_four_protocol_empty_budget_errors_are_explicit():
    for provider in ["openai", "anthropic", "gemini", "deepseek"]:
        p = profile(provider)
        raw = body(p.protocol, "")
        if p.protocol == "responses":
            raw.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        elif p.protocol == "messages":
            raw["stop_reason"] = "max_tokens"
        elif p.protocol == "gemini":
            raw["candidates"][0]["finishReason"] = "MAX_TOKENS"
        else:
            raw["choices"][0]["finish_reason"] = "length"
        with pytest.raises(AppError) as error:
            parse_turn(p, raw)
        assert error.value.code == Code.MODEL_OUTPUT_LIMIT


@pytest.mark.anyio
async def test_cancel_while_reading_closes_stream_and_next_call_works():
    entered, closed = asyncio.Event(), asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    calls = 0

    def respond(_request):
        nonlocal calls
        calls += 1
        return (
            httpx.Response(
                200, headers={"Content-Type": "application/json"}, stream=BlockingStream()
            )
            if calls == 1
            else httpx.Response(200, json=body("completions"))
        )

    g = ModelGateway(httpx.MockTransport(respond))
    try:
        task = asyncio.create_task(invoke(g))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
        await invoke(g)
        assert calls == 2
    finally:
        await g.close()


@pytest.mark.anyio
async def test_retry_after_cannot_exceed_remaining_logical_time():
    g, requests, waits = gateway(
        lambda _: httpx.Response(429, headers={"Retry-After": "2"}),
        policy=ModelRetryPolicy(timeout_seconds=1),
    )
    try:
        with pytest.raises(AppError) as error:
            await invoke(g)
        assert error.value.code == Code.MODEL_RATE_LIMITED and len(requests) == 1 and not waits
    finally:
        await g.close()


@pytest.mark.anyio
async def test_deepseek_generation_interruption_retries_without_budget_growth():
    raw = body("completions")
    raw["choices"][0]["finish_reason"] = "insufficient_system_resource"
    g, requests, _ = gateway(
        lambda n: httpx.Response(200, json=raw if n == 1 else body("completions"))
    )
    try:
        await invoke(g)
        assert len(requests) == 2 and requests[0] == requests[1]
    finally:
        await g.close()


def test_model_review_retry_keeps_one_decision_and_one_tool_execution(f):
    requests, review_attempts = [], []

    def respond(request):
        value = json.loads(request.content)
        requests.append(value)
        if value["messages"][0]["content"].startswith("You are Tavo's tool safety reviewer"):
            review_attempts.append(value)
            if len(review_attempts) == 1:
                return httpx.Response(503)
            payload = json.loads(value["messages"][-1]["content"])
            text = json.dumps(
                {
                    "decisions": [
                        {
                            "toolCallId": c["toolCallId"],
                            "action": "allow",
                            "reasonCode": "GOAL_ALIGNED",
                            "summary": "synthetic review",
                        }
                        for c in payload["calls"]
                    ]
                }
            )
            return httpx.Response(200, json=body("completions", text))
        return httpx.Response(200, json=tool_response("completions", final=len(requests) > 1))

    p = configure(f, "deepseek", respond, review_action=None)
    s = session(f, p.id, mode="auto_readonly")
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded" and run["toolCalls"][0]["attempts"] == 1
    assert len(review_attempts) == 2 and review_attempts[0] == review_attempts[1]
    assert len([d for d in run["toolCalls"][0]["decisions"] if d["source"] == "model"]) == 1
