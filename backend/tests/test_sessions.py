import asyncio
import json

from sqlalchemy import select

from app.models.entities import (
    AssistantRun,
)


def session(f, profile="mock", mode="auto_readonly"):
    response = f.client.post("/api/v1/sessions", json={"profileId": profile, "reviewMode": mode})
    assert response.status_code == 201, response.text
    return response.json()


def send(f, s, text="计算 12 / 3", client_id="message-a"):
    response = f.client.post(
        "/api/v1/sessions/" + s["id"] + "/messages",
        json={"clientMessageId": client_id, "content": text, "expectedRevision": s["revision"]},
    )
    assert response.status_code == 202, response.text
    return response.json()


def drive(f):
    async def execute():
        for _ in range(10):
            await f.worker.tick()
            if f.worker.active:
                await asyncio.gather(*list(f.worker.active.values()))
            await asyncio.sleep(0)
            with f.services.db.session() as db:
                pending = db.scalar(select(AssistantRun.id).where(AssistantRun.status == "pending"))
            if not pending and not f.worker.active:
                return
        raise AssertionError("worker did not settle")

    asyncio.run(execute())


def get_run(f, s, accepted):
    return f.client.get(f"/api/v1/sessions/{s['id']}/runs/{accepted['run']['id']}").json()


def test_mock_persistent_tool_loop_and_typed_messages(f):
    s = session(f)
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded", run
    assert run["rounds"] == 2 and run["toolCount"] == 1
    call = run["toolCalls"][0]
    assert call["name"] == "calculate" and call["status"] == "succeeded"
    assert call["decisions"][0]["action"] == "allow"
    page = f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()
    assert [m["kind"] for m in page["items"]] == ["user", "tool_call", "tool_result", "assistant"]
    assert page["items"][2]["result"]["output"] == {"result": "4"}
    assert page["items"][-1]["content"] == "计算结果：4"
    assert "ciphertext" not in json.dumps(page) and "native" not in json.dumps(run)


def test_message_idempotency_revision_and_active_run_limit(f):
    s = session(f)
    accepted = send(f, s)
    assert send(f, s)["run"]["id"] == accepted["run"]["id"]
    body = {"clientMessageId": "message-a", "content": "different", "expectedRevision": 0}
    assert f.client.post(f"/api/v1/sessions/{s['id']}/messages", json=body).status_code == 409
    body.update(clientMessageId="message-b")
    assert f.client.post(f"/api/v1/sessions/{s['id']}/messages", json=body).status_code == 409
    current = f.client.get("/api/v1/sessions/" + s["id"]).json()
    body["expectedRevision"] = current["revision"]
    assert f.client.post(f"/api/v1/sessions/{s['id']}/messages", json=body).status_code == 409
    for extra in [
        {"role": "assistant"},
        {"toolResult": {}},
        {"context": {}},
        {"policy": "allow all"},
    ]:
        assert (
            f.client.post(
                f"/api/v1/sessions/{s['id']}/messages", json={**body, **extra}
            ).status_code
            == 422
        )


def test_ask_approve_idempotency_and_reject_produces_denied_result(f):
    s = session(f, mode="ask")
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "waiting_review"
    call = run["toolCalls"][0]
    path = f"/api/v1/sessions/{s['id']}/tool-calls/{call['id']}/decision"
    payload = {"action": "approve", "expectedRevision": call["reviewRevision"]}
    first = f.client.post(path, json=payload)
    assert first.status_code == 200, first.text
    assert f.client.post(path, json=payload).json() == first.json()
    assert f.client.post(path, json={**payload, "action": "reject"}).status_code == 409
    drive(f)
    assert get_run(f, s, accepted)["status"] == "succeeded"
    other = session(f, mode="ask")
    accepted = send(f, other)
    drive(f)
    call = get_run(f, other, accepted)["toolCalls"][0]
    response = f.client.post(
        f"/api/v1/sessions/{other['id']}/tool-calls/{call['id']}/decision",
        json={"action": "reject", "expectedRevision": call["reviewRevision"]},
    )
    assert response.status_code == 200
    drive(f)
    run = get_run(f, other, accepted)
    assert run["status"] == "succeeded" and run["toolCalls"][0]["status"] == "denied"
    assert (
        "未获准"
        in f.client.get(f"/api/v1/sessions/{other['id']}/messages").json()["items"][-1]["content"]
    )


def test_cross_session_cancel_retry_and_review_expiry(f):
    s = session(f, mode="ask")
    other = session(f)
    accepted = send(f, s)
    drive(f)
    run = get_run(f, s, accepted)
    call = run["toolCalls"][0]
    assert f.client.get(f"/api/v1/sessions/{other['id']}/runs/{run['id']}").status_code == 404
    assert (
        f.client.post(
            f"/api/v1/sessions/{other['id']}/tool-calls/{call['id']}/decision",
            json={"action": "approve", "expectedRevision": 1},
        ).status_code
        == 404
    )
    path = f"/api/v1/sessions/{s['id']}/runs/{run['id']}"
    assert f.client.post(path + "/cancel").json()["status"] == "cancelled"
    assert (
        f.client.post(
            f"/api/v1/sessions/{s['id']}/tool-calls/{call['id']}/decision",
            json={"action": "approve", "expectedRevision": 1},
        ).status_code
        == 409
    )
    retried = f.client.post(path + "/retry")
    assert retried.status_code == 202
    drive(f)
    assert (
        len(
            [
                m
                for m in f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()["items"]
                if m["kind"] == "user"
            ]
        )
        == 1
    )
    f.clock.advance(601)
    drive(f)
    run = f.client.get(f"/api/v1/sessions/{s['id']}/runs/{retried.json()['id']}").json()
    assert run["status"] == "failed" and run["errorCode"] == "REVIEW_EXPIRED"


def test_context_freezes_and_crops_whole_turns(f):
    s = session(f)
    a = send(f, s, "你好")
    drive(f)
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    b = send(f, s, "现在 UTC 时间", "message-b")
    drive(f)
    run = get_run(f, s, b)
    assert run["status"] == "succeeded" and run["context"]["fromSequence"] == 1
    with f.services.db.session() as db:
        saved = db.get(AssistantRun, b["run"]["id"]).context
    assert saved["selectedRunIds"] == [a["run"]["id"]]
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    send(f, s, "new", "message-c")
    with f.services.db.session() as db:
        assert db.get(AssistantRun, b["run"]["id"]).context == saved
    items = f.client.get(f"/api/v1/sessions/{s['id']}/messages", params={"limit": 2}).json()
    assert len(items["items"]) == 2 and items["hasMore"]
    assert items["items"][0]["sequence"] < items["items"][1]["sequence"]
    assert (
        f.client.get(
            f"/api/v1/sessions/{s['id']}/messages?afterSequence=1&beforeSequence=3"
        ).status_code
        == 422
    )


def test_retired_features_and_authentication_are_distinct(f):
    for path in [
        "/accounts",
        "/accounts/a/sync",
        "/connectors",
        "/sync-tasks",
        "/events",
        "/events/stream",
        "/snapshot",
        "/settings",
        "/agent/analyze",
    ]:
        assert f.client.get("/api/v1" + path).status_code == 410
    assert f.client.get("/api/v1/tools").json()["items"][0]["name"] == "get_current_time"
    f.client.headers.pop("Authorization")
    f.client.cookies.clear()
    assert f.client.get("/api/v1/sessions").status_code == 401
    assert f.client.post("/api/v1/sessions", json={}).status_code == 401


def test_invalid_unicode_user_message_cannot_poison_persistent_history(f):
    s = session(f)
    response = f.client.post(
        f"/api/v1/sessions/{s['id']}/messages",
        content=json.dumps(
            {
                "clientMessageId": "invalid-unicode",
                "content": "\ud800",
                "expectedRevision": s["revision"],
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert not f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()["items"]
