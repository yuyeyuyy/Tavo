import json

import httpx
import pytest
from sqlalchemy import select

from app.models.entities import AssistantModelTurn
from tests.test_agent_protocols import configure, tool_response
from tests.test_sessions import drive, get_run, send, session


@pytest.mark.parametrize("action", ["deny", "ask"])
def test_model_review_can_deny_or_escalate_but_not_execute_itself(f, action):
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(200, json=tool_response("completions"))
        assert json.loads(request.content)["messages"][-1]["role"] == "tool"
        return httpx.Response(200, json=tool_response("completions", final=True))

    p = configure(f, "deepseek", respond, review_action=action)
    s = session(f, p.id)
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    assert run["toolCalls"][0]["attempts"] == 0
    assert run["toolCalls"][0]["decisions"][-1]["source"] == "model"
    assert run["status"] == ("waiting_review" if action == "ask" else "succeeded")
    if action == "deny":
        call = run["toolCalls"][0]
        assert (
            f.client.post(
                f"/api/v1/sessions/{s['id']}/tool-calls/{call['id']}/decision",
                json={"action": "approve", "expectedRevision": call["reviewRevision"]},
            ).status_code
            == 409
        )
    with f.services.db.session() as db:
        assert db.scalar(
            select(AssistantModelTurn).where(AssistantModelTurn.purpose == "self_review")
        ).ciphertext


@pytest.mark.parametrize(
    "bad",
    [
        "{}",
        '{"decisions":[]}',
        '{"decisions":[{"toolCallId":"other","action":"allow","reasonCode":"GOAL_ALIGNED","summary":"ok"}]}',
    ],
)
def test_malformed_review_fails_without_executing(f, bad):
    from app.agent.self_review import REVIEW_SYSTEM

    def respond(request):
        body = json.loads(request.content)
        raw = tool_response("completions")
        if json.dumps(REVIEW_SYSTEM) in json.dumps(body):
            assert not body.get("tools")
            raw = tool_response("completions", final=True)
            raw["choices"][0]["message"]["content"] = bad
        return httpx.Response(200, json=raw)

    p = configure(f, "deepseek", respond, review_action=None)
    s = session(f, p.id)
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    assert run["errorCode"] == "TOOL_REVIEW_FAILED"
    assert run["toolCalls"][0]["attempts"] == 0


def test_legacy_frozen_policy_stays_read_only_without_reinterpreting_approvals(f):
    import asyncio

    from app.agent.contracts import Context
    from app.models.entities import AssistantRun
    from tests.test_agent_recovery import claim

    s = session(f)
    a = send(f, s)
    scope = claim(f, a)
    asyncio.run(f.services.assistant.model(scope))
    with f.services.db.session(write=True) as db:
        run = db.get(AssistantRun, scope.run_id)
        value = Context.model_validate(run.context).model_dump(by_alias=True)
        value["policyVersion"] = "1"
        run.context = value
        run.status = "pending"
    drive(f)
    run = get_run(f, s, a)
    assert run["status"] == "succeeded"
    assert {d["source"] for d in run["toolCalls"][0]["decisions"]} == {"policy"}
