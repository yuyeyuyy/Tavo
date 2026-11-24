import asyncio
import json
import socket
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.agent.tools import ToolContext, ToolExecutor, ToolRegistry, define_tool
from app.agent.tools.builtin.web import SearchKeys, WebFetchArguments, WebSearchArguments, WebTools
from app.api.base import DTO
from app.cli import main
from app.core.errors import AppError, Code
from app.core.sandbox_config import default_policy
from app.models.entities import AssistantRun, AssistantToolCall, Secret
from app.repositories.sessions import call_digest
from tests.test_agent_protocols import configure, tool_response
from tests.test_agent_recovery import claim
from tests.test_sessions import drive, get_run, send, session


class TextArguments(DTO):
    text: str


class WordCount(DTO):
    count: int


def test_builtin_categories_and_explicit_extension_registration():
    registry = ToolRegistry()
    assert {s.name for s in registry.specs("host_and_sandbox", category="builtin")} >= {
        "sandbox",
        "host_bash",
        "calculate",
    }

    async def words(args, _context):
        return WordCount(count=len(args.text.split()))

    definition = define_tool(
        "count_words", "Count words in text.", TextArguments, WordCount, words, execution="async"
    )
    registry.register_extension(definition)
    assert registry.definitions["count_words"].category == "extension"
    (spec,) = registry.specs(category="extension")
    spec.input_schema.clear()
    assert registry.specs(category="extension")[0].input_schema["required"] == ["text"]
    with pytest.raises(AppError, match=Code.INVALID_INPUT):
        registry.register_extension(definition)
    with pytest.raises(AppError, match=Code.INVALID_INPUT):
        registry.register_extension(
            replace(definition, spec=definition.spec.model_copy(update={"read_only": False}))
        )

    async def run():
        executor = ToolExecutor(lambda: "")
        try:
            tool, args = registry.validate("count_words", {"text": "two words"})
            scope = SimpleNamespace(run_id="run")
            assert await executor.local(scope, tool, args, 1) == {"count": 2}
            with pytest.raises(AppError, match=Code.INVALID_DATA):
                executor.output(tool, {"wrong": 2})
            with pytest.raises(AppError, match=Code.TOOL_BUDGET_EXCEEDED):
                await executor.local(scope, tool, args, 0)
        finally:
            await executor.close()

    asyncio.run(run())


def test_sandbox_is_the_only_registered_sandbox_command():
    registry = ToolRegistry()
    sandbox_only = {spec.name for spec in registry.specs("sandbox_only")}
    full = {spec.name for spec in registry.specs("host_and_sandbox")}
    assert "sandbox" in sandbox_only
    assert full - sandbox_only == {"host_bash"}
    assert "bash" not in registry.definitions
    with pytest.raises(AppError, match=Code.FORBIDDEN):
        registry.validate("bash", {"command": "pwd"})


@pytest.mark.parametrize("version", ["2", "4"])
def test_retired_persisted_bash_call_is_denied_without_execution(f, monkeypatch, version):
    s = session(f)
    accepted = send(f, s)
    scope = claim(f, accepted)
    assistant = f.services.assistant
    _, context, _, _ = assistant.context.prepare(scope)
    arguments = {"command": "pwd"}
    with f.services.db.session(write=True) as db:
        run = db.get(AssistantRun, scope.run_id)
        run.rounds, run.step = 1, "tools"
        run.context = context.model_copy(
            update={"policy_version": version, "tool_names": ["bash"]}
        ).model_dump(by_alias=True)
        db.add(
            AssistantToolCall(
                id="retired-bash",
                run_id=scope.run_id,
                round=1,
                ordinal=0,
                provider_call_id="legacy",
                name="bash",
                arguments=arguments,
                argument_digest=call_digest("bash", arguments),
                status="approved",
                review_revision=1,
                attempts=0,
                created_at=f.clock.now(),
            )
        )

    async def unexpected(*_args, **_kwargs):
        pytest.fail("Retired tool reached command execution")

    monkeypatch.setattr(assistant.executor, "command", unexpected)
    asyncio.run(assistant.tools(scope))
    run = get_run(f, s, accepted)
    (call,) = run["toolCalls"]
    assert (call["name"], call["status"], call["attempts"]) == ("bash", "denied", 0)
    page = f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()
    assert page["items"][-1]["result"]["errorCode"] == Code.POLICY_DENIED


def public_dns(host, _port, **_kwargs):
    ip = "169.254.169.254" if host == "metadata.example.test" else "93.184.216.34"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


@pytest.fixture
def web(f):
    tools = WebTools(SearchKeys(f.services.secrets).get, resolver=public_dns)
    try:
        yield tools, ToolContext(lambda: "", web_policy=default_policy().network.model_dump())
    finally:
        tools.close()


def test_web_fetch_pins_address_checks_redirects_and_caps_output(web):
    tools, context = web
    hosts = []

    def respond(request):
        host = request.headers["host"]
        hosts.append(host)
        assert request.url.host == "93.184.216.34"
        assert request.extensions["sni_hostname"] == host
        assert request.headers["accept-encoding"] == "identity"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        if host == "docs.example.test":
            return httpx.Response(301, headers={"location": "https://source.example.test/guide"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<title>Docs</title><script>ignore me</script><p>" + "文" * 3000 + "</p>",
        )

    tools.transport = httpx.MockTransport(respond)
    result = tools.fetch(WebFetchArguments(url="https://docs.example.test/"), context)
    assert hosts == ["docs.example.test", "source.example.test"]
    assert result.url == "https://source.example.test/guide"
    assert result.title == "Docs" and result.truncated and set(result.text) == {"文"}
    assert len(result.text.encode()) <= 2600


@pytest.mark.parametrize(
    "destination",
    ["https://metadata.example.test/", "http://public.example.test/", "https://127.0.0.1/"],
)
def test_web_fetch_blocks_redirect_to_untrusted_target(web, destination):
    tools, context = web
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(301, headers={"location": destination})

    tools.transport = httpx.MockTransport(respond)
    with pytest.raises(AppError, match=Code.NETWORK_DENIED):
        tools.fetch(WebFetchArguments(url="https://docs.example.test"), context)
    assert len(requests) == 1


def test_web_search_key_is_only_sent_to_provider_and_missing_key_is_explicit(web, f):
    tools, context = web
    args = WebSearchArguments(query=" official guide ")
    with pytest.raises(AppError, match=Code.CREDENTIAL_UNAVAILABLE):
        tools.search(args, context)

    key = "synthetic-only-search-key"
    keys = SearchKeys(f.services.secrets)
    keys.put(None, SecretStr(key))
    with f.services.db.session() as db:
        assert key.encode() not in db.get(Secret, keys.reference(None)).ciphertext

    def respond(request):
        assert request.headers["host"] == "api.search.brave.com"
        assert request.headers["x-subscription-token"] == key
        assert request.url.params["q"] == "official guide"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Guide",
                            "url": "https://docs.example.test/guide#intro",
                            "description": "Preview",
                        },
                        {"title": "Unexpected", "url": "http://localhost/", "description": "Skip"},
                    ]
                }
            },
        )

    tools.transport = httpx.MockTransport(respond)
    result = tools.search(args, context)
    assert len(result.results) == 1
    assert result.results[0].url == "https://docs.example.test/guide"
    assert key not in result.model_dump_json()
    keys.remove(None)
    with pytest.raises(AppError, match=Code.CREDENTIAL_UNAVAILABLE):
        tools.search(args, context)


def test_search_key_cli_saves_and_removes_without_printing_plaintext(f, monkeypatch, capsys):
    import sys

    key = "synthetic-only-search-key"
    args = ["tavo", "web-search-key", "--data-dir", str(f.settings.data_dir)]
    monkeypatch.delenv("TAVO_DATA_DIR", raising=False)
    monkeypatch.delenv("TAVO_KEYRING_FILE", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda _: key)
    monkeypatch.setattr(sys, "argv", args)
    main()
    keys = SearchKeys(f.services.secrets)
    assert keys.get().get_secret_value() == key
    monkeypatch.setattr(sys, "argv", [*args, "--delete"])
    main()
    assert key not in capsys.readouterr().out
    with pytest.raises(AppError, match=Code.CREDENTIAL_UNAVAILABLE):
        keys.get()


def test_web_search_then_fetch_uses_durable_agent_loop(f):
    web = f.services.assistant.web
    web.resolver = public_dns
    SearchKeys(f.services.secrets).put(None, SecretStr("synthetic-only-search-key"))

    def public_web(request):
        if request.headers["host"] == "api.search.brave.com":
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Guide",
                                "url": "https://docs.example.test/guide",
                                "description": "A preview",
                            },
                        ]
                    }
                },
            )
        assert "x-subscription-token" not in request.headers
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="Official text")

    web.transport = httpx.MockTransport(public_web)
    turns = iter(
        [
            tool_response(
                "completions", "web_search", {"query": "official guide", "maxResults": 3}
            ),
            tool_response("completions", "web_fetch", {"url": "https://docs.example.test/guide"}),
            tool_response("completions", final=True),
        ]
    )

    def model(request):
        payload = json.loads(request.content)
        assert "synthetic-only-search-key" not in json.dumps(payload)
        assert {"web_search", "web_fetch"} <= {t["function"]["name"] for t in payload["tools"]}
        return httpx.Response(200, json=next(turns))

    profile = configure(f, "deepseek", model, review_action="deny")
    s = session(f, profile.id, mode="request_approval")
    accepted = send(f, s, "Find a public guide and read it.")
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded" and run["rounds"] == 3
    assert [(c["name"], c["status"]) for c in run["toolCalls"]] == [
        ("web_search", "succeeded"),
        ("web_fetch", "succeeded"),
    ]
    assert not f.services.assistant.gateway.review_requests
    messages = f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()["items"]
    outputs = [m["result"]["output"] for m in messages if m["kind"] == "tool_result"]
    assert outputs[0]["results"][0]["url"] == "https://docs.example.test/guide"
    assert outputs[1]["text"] == "Official text"


def test_frozen_run_does_not_gain_newly_registered_extension(f):
    s = session(f)
    scope = claim(f, send(f, s))
    builder = f.services.assistant.context
    _, context, _, _ = builder.prepare(scope)
    builder.registry.register_extension(
        define_tool(
            "test_word_count",
            "Count words in text.",
            TextArguments,
            WordCount,
            lambda _args, _ctx: WordCount(count=1),
        )
    )
    _, _, _, specs = builder.prepare(scope)
    assert {spec.name for spec in specs} == set(context.tool_names)
    assert "test_word_count" not in context.tool_names
