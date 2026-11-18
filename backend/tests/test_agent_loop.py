import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from app.agent.graph import Continue, LoopExit
from app.models.entities import AssistantRun, AssistantToolCall
from app.workers.scheduler import Scheduler
from tests.test_agent_protocols import configure, tool_response
from tests.test_agent_recovery import claim
from tests.test_sessions import get_run, send, session


def two_calculations(f, *, mode):
    first = tool_response("completions")
    calls = first["choices"][0]["message"]["tool_calls"]
    calls.append(
        {
            "id": "provider-2",
            "type": "function",
            "function": {
                "name": "calculate",
                "arguments": json.dumps({"operation": "add", "left": "7", "right": "2"}),
            },
        }
    )
    requests = []

    def respond(request):
        requests.append(json.loads(request.content)["messages"])
        return httpx.Response(
            200, json=first if len(requests) == 1 else tool_response("completions", final=True)
        )

    profile = configure(f, "deepseek", respond)
    conversation = session(f, profile.id, mode=mode)
    return conversation, send(f, conversation), requests


def run_once(f, accepted):
    async def execute():
        await f.worker.tick()
        result = await f.worker.active[accepted["run"]["id"]]
        assert f.worker.pool.available() == f.worker.pool.capacity
        return result

    return asyncio.run(execute())


def test_pause_releases_slot_and_all_decisions_resume_the_same_run(f):
    conversation, accepted, requests = two_calculations(f, mode="ask")
    assert run_once(f, accepted) is LoopExit.WAITING_REVIEW
    waiting = get_run(f, conversation, accepted)
    assert waiting["status"] == "waiting_review" and waiting["step"] == "tools"
    first, second = waiting["toolCalls"]
    assert [call["attempts"] for call in (first, second)] == [0, 0]

    def decide(call, action):
        response = f.client.post(
            f"/api/v1/sessions/{conversation['id']}/tool-calls/{call['id']}/decision",
            json={"action": action, "expectedRevision": call["reviewRevision"]},
        )
        assert response.status_code == 200

    decide(first, "approve")
    asyncio.run(f.worker.tick())
    assert not f.worker.active and len(requests) == 1
    assert get_run(f, conversation, accepted)["status"] == "waiting_review"

    f.clock.advance(3)
    f.worker = Scheduler(f.services)
    decide(second, "reject")
    assert get_run(f, conversation, accepted)["status"] == "pending"
    assert run_once(f, accepted) is LoopExit.COMPLETED
    finished = get_run(f, conversation, accepted)
    assert finished["id"] == waiting["id"]
    assert finished["status"] == "succeeded" and finished["rounds"] == 2
    assert {call["id"]: call["attempts"] for call in finished["toolCalls"]} == {
        first["id"]: 1,
        second["id"]: 0,
    }
    assert len(requests) == 2
    assert [
        (message["tool_call_id"], json.loads(message["content"])["status"])
        for message in requests[1]
        if message["role"] == "tool"
    ] == [("provider-1", "succeeded"), ("provider-2", "denied")]


def test_reclaim_tools_step_reuses_committed_result(f):
    conversation, accepted, requests = two_calculations(f, mode="auto_readonly")
    scope = claim(f, accepted)
    profile = f.services.models.profile(conversation["profileId"])
    with f.services.db.session(write=True) as db:
        db.get(AssistantRun, scope.run_id).profile = profile.model_dump()
    assert asyncio.run(f.services.assistant.model(scope)) == Continue("review")
    assert asyncio.run(f.services.assistant.review(scope)) == Continue("tools")

    # Simulate a worker exit after the transaction for the first tool has committed.
    with f.services.db.session(write=True) as db:
        first = db.scalar(
            select(AssistantToolCall).where(
                AssistantToolCall.run_id == scope.run_id, AssistantToolCall.ordinal == 0
            )
        )
        first.attempts = 1
        run = db.get(AssistantRun, scope.run_id)
        f.services.conversations.result(db, run, first, "succeeded", {"result": "4"})

    f.clock.advance(3)
    f.worker = Scheduler(f.services)
    assert run_once(f, accepted) is LoopExit.COMPLETED
    run = get_run(f, conversation, accepted)
    assert run["rounds"] == 2 and run["status"] == "succeeded"
    assert [call["attempts"] for call in run["toolCalls"]] == [1, 1]
    assert len(requests) == 2
    assert [
        json.loads(message["content"])["output"]["result"]
        for message in requests[1]
        if message["role"] == "tool"
    ] == ["4", "9"]


@pytest.mark.parametrize("outcome", [Continue("tools"), LoopExit.WAITING_REVIEW])
def test_model_step_rejects_unexpected_transition(f, monkeypatch, outcome):
    conversation = session(f)
    accepted = send(f, conversation)

    async def unexpected_transition(_scope):
        return outcome

    monkeypatch.setattr(f.services.assistant, "model", unexpected_transition)
    assert run_once(f, accepted) is None
    run = get_run(f, conversation, accepted)
    assert run["status"] == "failed" and run["errorCode"] == "INVALID_STATE"
    assert run["toolCalls"] == []
