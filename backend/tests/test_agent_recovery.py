import asyncio
import threading
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.agent.contracts import RunScope
from app.agent.tools import ToolRegistry
from app.core.errors import AppError
from app.models.entities import AssistantModelTurn, AssistantRun, AssistantToolCall, WorkerLease
from app.repositories.sessions import require_scope
from app.workers.scheduler import Scheduler
from tests.test_sessions import drive, get_run, send, session


def claim(f, accepted):
    assert f.worker.lead()
    with f.services.db.session(write=True) as db:
        row = db.get(AssistantRun, accepted["run"]["id"])
        row.status = "running"
        row.worker_owner, row.worker_fence = f.worker.owner, f.worker.fence
    return RunScope(row.id, row.session_id, f.worker.owner, f.worker.fence)


def test_unknown_billable_request_is_interrupted_without_replay(f):
    s = session(f)
    accepted = send(f, s)
    scope = claim(f, accepted)
    with f.services.db.session(write=True) as db:
        db.get(AssistantRun, scope.run_id).rounds = 1
        db.add(
            AssistantModelTurn(
                id="unknown-turn",
                run_id=scope.run_id,
                round=1,
                status="inflight",
                created_at=f.clock.now(),
            )
        )
    f.clock.advance(3)
    replacement = Scheduler(f.services)
    assert replacement.lead()
    assert get_run(f, s, accepted)["status"] == "interrupted"
    with f.services.db.session() as db:
        assert db.get(AssistantModelTurn, "unknown-turn").status == "unknown"
        with pytest.raises(AppError, match="LEASE_LOST"):
            require_scope(db, scope, f.clock.now())
    drive(f)
    assert get_run(f, s, accepted)["rounds"] == 1
    retried = f.client.post(f"/api/v1/sessions/{s['id']}/runs/{scope.run_id}/retry")
    assert retried.status_code == 202
    again = f.client.post(f"/api/v1/sessions/{s['id']}/runs/{scope.run_id}/retry")
    assert again.json()["id"] == retried.json()["id"]


def test_completed_model_proposal_recovers_without_repeating_first_round(f):
    s = session(f)
    accepted = send(f, s)
    scope = claim(f, accepted)
    asyncio.run(f.services.assistant.model(scope))
    assert get_run(f, s, accepted)["rounds"] == 1
    f.clock.advance(3)
    replacement = Scheduler(f.services)
    assert replacement.lead()
    f.worker = replacement
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded" and run["rounds"] == 2
    assert len(run["toolCalls"]) == 1 and run["toolCalls"][0]["attempts"] == 1


def test_cancel_rejects_late_model_response(f):
    s = session(f)
    accepted = send(f, s)
    original = f.services.assistant.mock

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        class Delayed:
            async def turn(self, *args):
                started.set()
                await release.wait()
                return await original.turn(*args)

        f.services.assistant.mock = Delayed()
        await f.worker.tick()
        await started.wait()
        response = f.client.post(f"/api/v1/sessions/{s['id']}/runs/{accepted['run']['id']}/cancel")
        assert response.status_code == 200
        release.set()
        await asyncio.gather(*list(f.worker.active.values()))

    asyncio.run(exercise())
    assert get_run(f, s, accepted)["status"] == "cancelled"
    with f.services.db.session() as db:
        assert db.scalar(select(AssistantModelTurn)).status == "unknown"
    messages = f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()["items"]
    assert [m["kind"] for m in messages] == ["user"]


def test_timed_out_tool_keeps_slot_and_late_result_is_discarded(f):
    s = session(f)
    accepted = send(f, s)
    registry = ToolRegistry()
    definition = registry.definitions["calculate"]
    started, release = threading.Event(), threading.Event()

    def slow(args, context):
        started.set()
        release.wait(3)
        return definition.execute(args, context)

    registry.definitions["calculate"] = replace(definition, execute=slow)
    f.services.assistant.registry = registry
    f.services.assistant.reviewer.registry = registry
    f.services.assistant.context.registry = registry
    f.services.assistant.tool_timeout = 0.01

    async def exercise():
        await f.worker.tick()
        await asyncio.gather(*list(f.worker.active.values()))
        assert started.is_set()
        assert accepted["run"]["id"] in f.services.assistant.busy_slots()
        page = f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()["items"]
        result = next(m["result"] for m in page if m["kind"] == "tool_result")
        assert result["status"] == "failed" and result["errorCode"] == "TIMEOUT"
        release.set()
        await asyncio.gather(*list(f.services.assistant.readers.values()))
        await asyncio.sleep(0)
        assert not f.services.assistant.busy_slots()
        assert page == f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()["items"]

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_round_limit_stops_repeated_tool_proposals(f):
    s = session(f)
    accepted = send(f, s)
    original = f.services.assistant.mock

    class Repeating:
        async def turn(self, profile, history, specs):
            return await original.turn(profile, history[:1], specs)

    f.services.assistant.mock = Repeating()
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "failed" and run["errorCode"] == "TOOL_BUDGET_EXCEEDED"
    assert run["rounds"] == 4 and run["toolCount"] == 4


def test_context_drops_only_complete_turns_and_preserves_current_user(f):
    s = session(f)
    first = send(f, s)
    drive(f)
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    second = send(f, s, "解释一下上下文", "second")
    scope = claim(f, second)
    builder = f.services.assistant.context
    with f.services.db.session() as db:
        prior = builder.history_in(db, [first["run"]["id"]], "completions")
    assert any(item.get("role") == "tool" for item in prior)
    builder.budget = 1400
    _, context, history, _ = builder.prepare(scope)
    assert context.dropped_turns == 1 and not context.selected_run_ids
    assert history[-1]["content"] == "解释一下上下文" and len(history) == 1
    builder.budget = 1
    with pytest.raises(AppError, match="CONTEXT_TOO_LARGE"):
        builder.prepare(scope)


def test_worker_cannot_commit_after_another_owner_takes_lease(f):
    s = session(f)
    accepted = send(f, s)
    scope = claim(f, accepted)
    with f.services.db.session(write=True) as db:
        lease = db.get(WorkerLease, 1)
        lease.owner, lease.fence = "replacement", lease.fence + 1
    asyncio.run(f.services.assistant.run(scope))
    with f.services.db.session() as db:
        assert not list(db.scalars(select(AssistantModelTurn)))
        assert not list(db.scalars(select(AssistantToolCall)))
