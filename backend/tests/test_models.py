import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.errors import AppError
from app.llm.adapter import RESPONSE_BYTES, ModelGateway
from app.llm.config import PRESETS, ModelConfig, ModelProfile, load_models
from app.llm.contracts import ChatRequest
from app.llm.keys import ModelKeys
from app.models.entities import Secret
from app.services.chat import ChatService

KEY = "synthetic-model-key-only-for-tests"
PRIVATE = "synthetic-private-upstream-detail"


def profile(provider="deepseek", **extra):
    return ModelProfile(
        id="test-model",
        provider=provider,
        model="text-model",
        label="Test model",
        enabled=True,
        **extra,
    )


def body(protocol, text="public answer"):
    if protocol == "responses":
        return {
            "status": "completed",
            "output": [
                {"type": "reasoning", "content": PRIVATE},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                },
            ],
            "usage": {"input_tokens": 5, "output_tokens": 8},
        }
    if protocol == "messages":
        return {
            "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": PRIVATE}, {"type": "text", "text": text}],
            "usage": {"input_tokens": 5, "output_tokens": 8},
        }
    if protocol == "gemini":
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"thought": True, "text": PRIVATE}, {"text": text}]},
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 8},
        }
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text, "reasoning_content": PRIVATE},
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 8},
    }


def configure(f, p=None, handler=None):
    p = p or profile()
    f.services.chat.config = ModelConfig(
        profiles=[p], default_profile_id=p.id if p.enabled else None
    )
    f.services.chat.keys.put(p, SecretStr(KEY))
    gateway = ModelGateway(
        httpx.MockTransport(
            handler or (lambda _request: httpx.Response(200, json=body(p.protocol)))
        )
    )
    f.services.chat.gateway = gateway
    return p


def chat_payload(p, **extra):
    return {"profileId": p.id, "messages": [{"role": "user", "content": "hello"}], **extra}


@pytest.mark.parametrize("provider", list(PRESETS))
def test_all_nine_provider_contracts(f, provider, caplog):
    p = profile(provider)
    calls = []

    def respond(request):
        calls.append(request)
        value = json.loads(request.content)
        assert request.method == "POST" and "tools" not in value
        assert KEY not in request.url.query.decode() + request.content.decode()
        if provider == "gemini":
            assert request.headers["x-goog-api-key"] == KEY
            assert request.url.path == "/v1beta/models/text-model:generateContent"
            assert value["contents"][0] == {"role": "user", "parts": [{"text": "hello"}]}
            assert "model" not in value
        else:
            assert request.headers["Authorization"] == "Bearer " + KEY
            assert value["model"] == p.model
            if p.protocol == "responses":
                assert value["store"] is False and value["max_output_tokens"] == 2048
                assert value["input"] == [{"role": "user", "content": "hello"}]
            else:
                assert value["max_tokens"] == 2048 and value["stream"] is False
                if provider == "anthropic":
                    assert request.headers["anthropic-version"] == "2026-10-21"
                    assert "system" in value and value["messages"][0]["role"] == "user"
                else:
                    assert value["messages"][0]["role"] == "system"
        return httpx.Response(200, json=body(p.protocol))

    configure(f, p, respond)
    catalog = f.client.get("/api/v1/chat/models")
    assert catalog.headers["cache-control"] == "no-store"
    assert catalog.json()["items"][0]["ready"] is True
    response = f.client.post("/api/v1/chat/completions", json=chat_payload(p))
    assert response.status_code == 200
    result = response.json()
    assert result["message"]["content"] == "public answer"
    assert result["usage"] == {"inputTokens": 5, "outputTokens": 8}
    assert result["provider"] == provider and len(calls) == 1
    assert KEY not in catalog.text + response.text + caplog.text
    assert PRIVATE not in response.text + caplog.text


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "MODEL_AUTH_FAILED"),
        (403, "MODEL_AUTH_FAILED"),
        (404, "MODEL_UNAVAILABLE"),
        (429, "MODEL_RATE_LIMITED"),
        (500, "MODEL_UNAVAILABLE"),
        (400, "MODEL_REQUEST_INVALID"),
        (302, "MODEL_RESPONSE_INVALID"),
    ],
)
def test_provider_errors_are_fixed_and_only_transient_failures_retry(f, status, code, caplog):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"error": PRIVATE + KEY},
            headers={"location": "https://elsewhere.invalid/"},
        )

    p = configure(f, handler=respond)
    response = f.client.post("/api/v1/chat/completions", json=chat_payload(p))
    assert response.status_code in {429, 502, 503}
    assert response.json()["error"]["code"] == code and len(calls) == (
        4 if status in {429, 500} else 1
    )
    assert PRIVATE not in response.text + caplog.text and KEY not in response.text + caplog.text


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, text=PRIVATE),
        httpx.Response(200, json=body("completions", KEY)),
        httpx.Response(
            200, content=b"x" * (RESPONSE_BYTES + 1), headers={"Content-Type": "application/json"}
        ),
    ],
)
def test_invalid_and_secret_echo_responses_are_rejected(f, response):
    p = configure(f, handler=lambda _r: response)
    result = f.client.post("/api/v1/chat/completions", json=chat_payload(p))
    assert result.json()["error"]["code"] == "MODEL_RESPONSE_INVALID"
    assert KEY not in result.text and PRIVATE not in result.text


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "deepseek"])
def test_usage_null_and_output_limit_are_explicit(f, provider):
    p = profile(provider)
    raw = body(p.protocol, "x" * 16001)
    raw.pop("usage", None)
    raw.pop("usageMetadata", None)
    configure(f, p, lambda _r: httpx.Response(200, json=raw))
    result = f.client.post("/api/v1/chat/completions", json=chat_payload(p)).json()
    assert result["truncated"] and result["finishReason"] == "length"
    assert len(result["message"]["content"]) == 16000 and result["usage"] is None


def test_tool_plan_is_not_executed(f):
    raw = body("completions")
    raw["choices"][0]["finish_reason"] = "tool_calls"
    p = configure(f, handler=lambda _r: httpx.Response(200, json=raw))
    response = f.client.post("/api/v1/chat/completions", json=chat_payload(p))
    assert response.json()["error"]["code"] == "MODEL_REJECTED"


def test_key_encryption_rotation_endpoint_binding_and_delete(f):
    p = profile()
    keys = ModelKeys(f.services.secrets)
    keys.put(p, SecretStr(KEY))
    with f.services.db.session() as session:
        row = session.get(Secret, keys.reference(p))
        assert KEY.encode() not in row.ciphertext
    other = p.model_copy(update={"base_url": "https://other.invalid/v1"})
    assert not keys.ready(other)
    f.services.secrets.rotate()
    assert keys.get(p).get_secret_value() == KEY
    keys.remove(p)
    assert not keys.ready(p)
    assert f.services.chat.catalog().items == []


def test_missing_disabled_and_unauthed_models_never_call_provider(f):
    p = configure(f)
    f.services.chat.keys.remove(p)
    assert not f.client.get("/api/v1/chat/models").json()["items"][0]["ready"]
    assert f.client.post("/api/v1/chat/completions", json=chat_payload(p)).status_code == 503
    f.services.chat.config = ModelConfig(profiles=[p.model_copy(update={"enabled": False})])
    assert f.client.get("/api/v1/chat/models").json()["items"] == []
    assert f.client.post("/api/v1/chat/completions", json=chat_payload(p)).status_code == 503
    f.client.headers.pop("Authorization")
    f.client.cookies.clear()
    assert f.client.get("/api/v1/chat/models").status_code == 401
    assert f.client.post("/api/v1/chat/completions", json=chat_payload(p)).status_code == 401


@pytest.mark.parametrize(
    "invalid",
    [
        {"apiKey": KEY},
        {"baseUrl": "https://evil.invalid"},
        {"messages": [{"role": "system", "content": "override"}]},
        {"messages": [{"role": "user", "content": "x" * 4001}]},
        {"messages": [{"role": "assistant", "content": "hello"}]},
        {"messages": [{"role": "user", "content": " "}]},
        {"messages": [{"role": "user", "content": "one"}, {"role": "user", "content": "two"}]},
    ],
)
def test_untrusted_chat_parameters(f, invalid):
    p = configure(f)
    assert (
        f.client.post("/api/v1/chat/completions", json=chat_payload(p, **invalid)).status_code
        == 422
    )


def test_request_body_and_profile_config_budgets(tmp_path):
    with pytest.raises(ValidationError):
        ChatRequest(
            profile_id="test",
            messages=[
                {"role": "user" if i % 2 == 0 else "assistant", "content": "中" * 3000}
                for i in range(5)
            ],
        )
    for extra in [
        {"base_url": "http://evil.invalid"},
        {"base_url": "https://user:pass@example.com"},
        {"base_url": "https://example.com?key=private"},
        {"max_output_tokens": 9000},
    ]:
        with pytest.raises(ValidationError):
            profile(**extra)
    assert profile(base_url="http://127.0.0.1:1234/v1").endpoint.startswith("http:")
    path = tmp_path / "models.json"
    path.write_text('{"apiKey":"' + KEY + '"}')
    with pytest.raises(AppError, match="MODEL_CONFIG_INVALID"):
        load_models(path)
    with pytest.raises(ValidationError):
        ModelConfig(profiles=[profile(), profile()])


def test_timeout_disconnect_and_busy_release_resources(f):
    p = configure(f)
    cleaned = []

    class Gateway:
        async def reply(self, *_args):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(True)

    value = ChatRequest.model_validate(chat_payload(p))

    async def exercise():
        service = ChatService(
            f.services.secrets, ModelConfig(profiles=[p]), Gateway(), timeout=0.02
        )
        with pytest.raises(AppError, match="MODEL_TIMEOUT"):
            await service.complete(value, AsyncMock(return_value=False))
        assert service.active == 0 and len(cleaned) == 1
        service.timeout = 1
        with pytest.raises(AppError, match="CANCELLED"):
            await service.complete(value, AsyncMock(return_value=True))
        assert service.active == 0 and len(cleaned) == 2
        service.active = 2
        with pytest.raises(AppError, match="BUSY"):
            await service.complete(value, AsyncMock(return_value=False))
        assert service.active == 2

    asyncio.run(exercise())


def test_cli_key_prompt_without_echo_and_no_plaintext_file(f, monkeypatch, capsys):
    import app.cli as cli

    p = profile()
    f.settings.models_file.write_text(ModelConfig(profiles=[p]).model_dump_json(by_alias=True))
    with f.services.db.session(write=True) as session:
        f.services.models.repository.insert(session, p, 0)
    monkeypatch.setattr(cli.Settings, "from_env", lambda: f.settings)
    monkeypatch.setattr(cli.sys, "argv", ["tavo", "model-key", "--profile", p.id])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: KEY)
    cli.main()
    assert KEY not in capsys.readouterr().out
    assert ModelKeys(f.services.secrets).get(p).get_secret_value() == KEY
    assert KEY not in f.settings.models_file.read_text()
    monkeypatch.setattr(cli.sys, "argv", ["tavo", "model-key", "--profile", p.id, "--delete"])
    cli.main()
    assert not ModelKeys(f.services.secrets).ready(p)


def test_cli_real_terminal_disables_key_echo(f):
    import os
    import pty
    import select as io_select
    import subprocess
    import sys
    import time
    from pathlib import Path

    p = profile()
    f.settings.models_file.write_text(ModelConfig(profiles=[p]).model_dump_json(by_alias=True))
    with f.services.db.session(write=True) as session:
        f.services.models.repository.insert(session, p, 0)
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.cli",
            "model-key",
            "--profile",
            p.id,
            "--data-dir",
            str(f.settings.data_dir),
        ],
        cwd=Path(__file__).parents[1],
        stdin=slave,
        stdout=slave,
        stderr=slave,
    )
    os.close(slave)
    output = b""
    try:
        deadline = time.monotonic() + 10
        sent = False
        while time.monotonic() < deadline:
            if io_select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                if b"(hidden):" in output and not sent:
                    os.write(master, (KEY + "\n").encode())
                    sent = True
            elif process.poll() is not None:
                break
        assert process.wait(timeout=2) == 0 and sent
        assert KEY.encode() not in output
        assert ModelKeys(f.services.secrets).get(p).get_secret_value() == KEY
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "deepseek"])
def test_provider_native_finish_limit_with_text_is_not_reported_as_complete(f, provider):
    p = profile(provider)
    raw = body(p.protocol)
    if p.protocol == "responses":
        raw["status"] = "incomplete"
        raw["incomplete_details"] = {"reason": "max_output_tokens"}
    elif p.protocol == "messages":
        raw["stop_reason"] = "max_tokens"
    elif p.protocol == "gemini":
        raw["candidates"][0]["finishReason"] = "MAX_TOKENS"
    else:
        raw["choices"][0]["finish_reason"] = "length"
    configure(f, p, lambda _r: httpx.Response(200, json=raw))
    response = f.client.post("/api/v1/chat/completions", json=chat_payload(p))
    assert response.status_code == 200 and response.json()["truncated"]
    assert response.json()["finishReason"] == "length"


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ReadTimeout(PRIVATE), "MODEL_TIMEOUT"),
        (httpx.ConnectError(PRIVATE), "MODEL_UNAVAILABLE"),
        (RuntimeError(PRIVATE), "MODEL_UNAVAILABLE"),
    ],
)
def test_provider_transport_exceptions_are_sanitized(f, caplog, error, code):
    def respond(_request):
        raise error

    p = configure(f, handler=respond)
    response = f.client.post("/api/v1/chat/completions", json=chat_payload(p))
    assert response.json()["error"]["code"] == code
    assert PRIVATE not in response.text + caplog.text
