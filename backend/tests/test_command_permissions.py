import asyncio
import json

import httpx
import pytest

from app.agent.tools import ToolRegistry
from app.core.sandbox_config import default_policy
from app.llm.prompts import CONVERSATION_STYLE
from app.llm.turns import turn_request
from tests.test_agent_protocols import configure, tool_response
from tests.test_agent_recovery import claim
from tests.test_sessions import drive, get_run, send, session

MODES = ["full_access", "auto_review", "request_approval"]


def enable_schema(f):
    registry = ToolRegistry(bash_enabled=True)
    f.services.bash.available = True
    f.services.assistant.registry = registry
    f.services.assistant.context.registry = registry
    f.services.assistant.reviewer.registry = registry


@pytest.mark.parametrize("mode", MODES)
def test_non_bash_tools_are_automatic_without_model_or_human_review(f, mode):
    profile = configure(
        f,
        "deepseek",
        lambda request: httpx.Response(
            200,
            json=tool_response(
                "completions", final=json.loads(request.content)["messages"][-1]["role"] == "tool"
            ),
        ),
        review_action="deny",
    )
    s = session(f, profile.id, mode)
    a = send(f, s)
    drive(f)
    run = get_run(f, s, a)
    assert run["status"] == "succeeded", run
    assert run["toolCalls"][0]["status"] == "succeeded"
    assert {d["source"] for d in run["toolCalls"][0]["decisions"]} == {"policy"}
    assert not f.services.assistant.gateway.review_requests


@pytest.mark.parametrize(
    "mode,model_action,expected,status",
    [
        ("full_access", "deny", "allow", "running"),
        ("auto_review", "allow", "allow", "running"),
        ("auto_review", "deny", "deny", "running"),
        ("auto_review", "ask", "ask", "waiting_review"),
        ("request_approval", "allow", "ask", "waiting_review"),
    ],
)
def test_bash_permission_decisions_before_any_execution(f, mode, model_action, expected, status):
    enable_schema(f)
    profile = configure(
        f,
        "deepseek",
        lambda _: httpx.Response(
            200, json=tool_response("completions", "sandbox", {"command": "pwd"})
        ),
        review_action=model_action,
    )
    s = session(f, profile.id, mode)
    a = send(f, s)
    scope = claim(f, a)
    from app.models.entities import AssistantRun

    with f.services.db.session(write=True) as db:
        db.get(AssistantRun, scope.run_id).profile = profile.model_dump()

    async def review():
        await f.services.assistant.model(scope)
        await f.services.assistant.review(scope)

    asyncio.run(review())
    run = get_run(f, s, a)
    call = run["toolCalls"][0]
    assert call["name"] == "sandbox", call
    assert run["context"]["policyVersion"] == "5"
    assert run["status"] == status
    assert call["attempts"] == 0
    assert call["decisions"][-1]["action"] == expected
    assert all(d["source"] != "human" for d in call["decisions"])
    assert bool(f.services.assistant.gateway.review_requests) == (mode == "auto_review")


def test_new_defaults_and_legacy_context_do_not_expand_network(f):
    from app.agent.contracts import SessionCreate
    from app.models.entities import AssistantRun

    assert SessionCreate(profileId="mock").review_mode == "full_access"
    assert default_policy().network.mode == "proxy"
    enable_schema(f)
    for mode, version, public, approval in [
        ("full_access", "5", True, False),
        ("auto_sandbox", "3", False, True),
    ]:
        s = session(f, mode=mode)
        a = send(f, s)
        scope = claim(f, a)
        f.services.assistant.context.prepare(scope)
        network = f.services.bash.manager.egress.network_for_run(scope.run_id)
        assert network["allow_public_https"] is public
        assert network["require_write_approval"] is approval
        with f.services.db.session() as db:
            assert db.get(AssistantRun, scope.run_id).context["policyVersion"] == version
        # Release the synthetic active run before claiming the next session.
        with f.services.db.session(write=True) as db:
            db.get(AssistantRun, scope.run_id).status = "succeeded"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "deepseek"])
def test_conversation_style_is_shared_across_protocols(f, provider):
    profile = configure(f, provider, lambda _: None)
    payload = turn_request(profile, [], [])
    assert CONVERSATION_STYLE in json.dumps(payload, ensure_ascii=False).replace("\\n", "\n")


def test_mock_describes_tasks_without_listing_internal_tools(f):
    s = session(f, mode="full_access")
    send(f, s, "你有什么工具？")
    drive(f)
    messages = f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()["items"]
    text = messages[-1]["content"]
    assert "分析问题" in text
    assert not any(name in text for name in ToolRegistry(bash_enabled=True).definitions)
