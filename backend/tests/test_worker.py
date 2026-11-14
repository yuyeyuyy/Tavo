import asyncio
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select

from app.agent.graph import create_graph
from app.core.errors import AppError
from app.models.entities import Account, AccountData, BusinessEvent, SyncTask
from app.repositories.workspace import require_leader
from app.workers.scheduler import Scheduler, retry_delay


def process(f):
    async def run():
        await f.worker.tick()
        await f.worker.drain()

    asyncio.run(run())


def enqueue(f, account_id):
    result = f.client.post(f"/api/v1/accounts/{account_id}/sync")
    assert result.status_code == 202
    return result.json()


def test_sync_incremental_events_and_agent(f):
    a = f.add()
    job = enqueue(f, a["id"])
    assert f.client.post(f"/api/v1/accounts/{a['id']}/sync").status_code == 409
    process(f)
    assert f.client.get("/api/v1/sync-tasks/" + job["id"]).json()["status"] == "succeeded"
    snapshot = f.client.get("/api/v1/snapshot").json()
    assert snapshot["data"][0]["balance"] == "8.50"
    events = f.client.get("/api/v1/events", params={"correlationId": job["id"]}).json()["items"]
    assert [row["eventType"] for row in reversed(events)] == [
        "sync.started",
        "data.changed",
        "sync.completed",
    ]
    changed = next(row for row in events if row["eventType"] == "data.changed")
    run = f.client.post("/api/v1/agent/analyze", json={"eventId": changed["eventId"]})
    assert run.status_code == 202
    process(f)
    result = f.client.get("/api/v1/agent/runs/" + run.json()["id"]).json()
    assert result["status"] == "succeeded" and result["progress"] == 100
    assert result["result"]["severity"] == "warning"
    enqueue(f, a["id"])
    process(f)
    assert (
        len(f.client.get("/api/v1/events", params={"eventType": "data.changed"}).json()["items"])
        == 1
    )
    config = {**a["mock"], "balance": "3.00", "usageUnits": 120, "resourceStatus": "degraded"}
    assert f.client.patch(f"/api/v1/accounts/{a['id']}/mock", json=config).status_code == 200
    enqueue(f, a["id"])
    process(f)
    assert (
        len(f.client.get("/api/v1/events", params={"eventType": "data.changed"}).json()["items"])
        == 2
    )
    first = f.client.get("/api/v1/events?limit=2").json()
    second = f.client.get(
        "/api/v1/events", params={"before": first["nextCursor"], "limit": 2}
    ).json()
    assert second["items"][0]["sequence"] < first["items"][-1]["sequence"]


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("network_once", "NETWORK"),
        ("rate_limit_once", "RATE_LIMITED"),
        ("server_once", "SERVER_ERROR"),
    ],
)
def test_retry(f, scenario, code):
    a = f.add()
    f.client.patch(f"/api/v1/accounts/{a['id']}/mock", json={**a["mock"], "scenario": scenario})
    job = enqueue(f, a["id"])
    process(f)
    pending = f.services.task(job["id"])
    assert pending.status == "pending" and pending.error_code == code
    f.clock.advance(2)
    process(f)
    assert f.services.task(job["id"]).status == "succeeded"
    assert f.services.task(job["id"]).retry_count == 1


@pytest.mark.parametrize(
    "scenario,code,status",
    [
        ("auth", "AUTH_EXPIRED", "expired"),
        ("forbidden", "PERMISSION_DENIED", "error"),
        ("invalid", "INVALID_DATA", "error"),
    ],
)
def test_permanent_errors(f, scenario, code, status):
    a = f.add()
    f.client.patch(f"/api/v1/accounts/{a['id']}/mock", json={**a["mock"], "scenario": scenario})
    job = enqueue(f, a["id"])
    process(f)
    assert f.services.task(job["id"]).status == "failed"
    assert f.services.task(job["id"]).error_code == code
    assert f.services.accounts.get(a["id"]).status == status


def test_timeout_bounded_and_preserves_old_data(f):
    a = f.add()
    enqueue(f, a["id"])
    process(f)
    f.client.patch(
        f"/api/v1/accounts/{a['id']}/mock",
        json={**a["mock"], "balance": "1.00", "scenario": "timeout"},
    )
    job = enqueue(f, a["id"])
    for _ in range(3):
        process(f)
        f.clock.advance(5)
    assert f.services.task(job["id"]).status == "failed"
    assert f.services.task(job["id"]).attempts == 3
    with f.services.db.session() as session:
        assert session.get(AccountData, a["id"]).balance == "8.50"
        assert session.get(Account, a["id"]).cursor == "revision:1"


def test_commit_rollback(f):
    a = f.add()
    with f.services.db.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER fail_commit BEFORE INSERT ON business_events WHEN NEW.event_type='sync.completed' BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
    job = enqueue(f, a["id"])
    process(f)
    assert f.services.task(job["id"]).status == "failed"
    with f.services.db.session() as session:
        assert session.get(AccountData, a["id"]) is None
        assert session.get(Account, a["id"]).cursor is None
        assert not list(
            session.scalars(select(BusinessEvent).where(BusinessEvent.event_type == "data.changed"))
        )


def test_single_leader_recovery_and_fencing(f):
    a = f.add()
    job = enqueue(f, a["id"])
    assert f.worker.lead()
    with f.services.db.session(write=True) as session:
        row = session.get(SyncTask, job["id"])
        row.status, row.attempts, row.started_at, row.worker_owner, row.worker_fence = (
            "running",
            1,
            f.clock.now(),
            f.worker.owner,
            f.worker.fence,
        )
    second = Scheduler(f.services)
    assert not second.lead()
    f.clock.advance(3)
    assert second.lead()
    with f.services.db.session() as session:
        assert session.get(SyncTask, job["id"]).status == "pending"
        with pytest.raises(AppError, match="LEASE_LOST"):
            require_leader(session, f.worker.owner, f.worker.fence, f.clock.now())

    async def finish():
        await second.tick()
        await second.drain()
        await second.close()

    asyncio.run(finish())
    assert f.services.task(job["id"]).status == "succeeded"
    assert f.services.task(job["id"]).attempts == 2


def test_scheduling_and_global_limit(f):
    a, b = f.add(), f.add("b")
    f.services.settings = replace(f.settings, concurrency=1)
    f.client.patch(
        "/api/v1/settings",
        json={"autoSync": True, "syncIntervalSeconds": 10, "startMinimized": False},
    )
    f.clock.advance(10)
    process(f)
    jobs = f.services.tasks().items
    assert sum(row.status == "succeeded" for row in jobs) == 1
    assert sum(row.status == "pending" for row in jobs) == 1
    process(f)
    assert all(row.status == "succeeded" for row in f.services.tasks().items)
    assert {row.connection_id for row in jobs} == {a["id"], b["id"]}


@pytest.mark.parametrize(
    "currency,balance,severity",
    [
        ("USD", "8.50", "warning"),
        ("USD", "10.00", "info"),
        ("CNY", "49.99", "warning"),
        ("CNY", "50.00", "info"),
    ],
)
def test_graph_equivalent_to_typescript(currency, balance, severity):
    graph = create_graph()
    result = graph.invoke({"event": {"id": "event", "balance": balance, "currency": currency}})
    assert result["analysis"]["severity"] == severity
    assert result["analysis"]["mode"] == "deterministic-mock"
    assert f"模拟余额：{balance} {currency}" in result["analysis"]["evidence"]


def test_checkpoint_extension():
    graph = create_graph(MemorySaver())
    config = {"configurable": {"thread_id": "demo"}}
    graph.invoke({"event": {"id": "event", "balance": "0.00", "currency": "USD"}}, config)
    assert graph.get_state(config).values["analysis"]["severity"] == "warning"
    assert not graph.get_state(config).next


def test_backoff():
    assert retry_delay(1, lambda: 0) == 0.5
    assert retry_delay(2, lambda: 1) == 2
    assert retry_delay(100, lambda: 1) == 30
    assert retry_delay(1, lambda: 0, 20) == 20
