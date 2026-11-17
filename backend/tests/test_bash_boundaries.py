import asyncio
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.agent.bash import BashArguments
from app.models.entities import AssistantToolCall, BashExecution, ConversationSession
from app.repositories.sessions import call_digest
from app.workers.scheduler import Scheduler
from tests.test_agent_recovery import claim
from tests.test_sessions import get_run, send, session


@pytest.mark.parametrize(
    "value",
    [
        {"command": " "},
        {"command": "echo hi", "cwd": "../private"},
        {"command": "ls", "cwd": "/data"},
        {"command": "x" * 32769},
        {"command": "命" * 10923},
        {"command": "ls", "timeoutSeconds": True},
        {"command": "ls", "mount": "/Users"},
        {"command": "ls", "cwd": "x\x00"},
    ],
)
def test_bash_parameters_cannot_expand_host_capabilities(value):
    with pytest.raises(ValidationError):
        BashArguments.model_validate(value)


def test_bash_defaults_on_and_missing_docker_does_not_prevent_text_chat(f):
    assert f.client.get("/api/v1/agent-runtime").json()["bash"]["available"] is False
    for mode, enabled in [
        ("auto_sandbox", False),
        ("disabled", True),
        ("ask", "true"),
    ]:
        r = f.client.post(
            "/api/v1/sessions",
            json={"profileId": "mock", "reviewMode": mode, "bashEnabled": enabled},
        )
        assert r.status_code == 422
    s = session(f)
    assert s["bashEnabled"]
    accepted = send(f, s, "hello")
    from tests.test_sessions import drive

    drive(f)
    assert get_run(f, s, accepted)["status"] == "succeeded"
    pure = f.client.post("/api/v1/sessions", json={"profileId": "mock", "reviewMode": "disabled"})
    assert pure.status_code == 201 and pure.json()["bashEnabled"] is False
    assert {t["name"] for t in f.client.get("/api/v1/tools").json()["items"]} == {
        "calculate",
        "get_current_time",
        "host_bash",
    }


def test_unknown_write_is_never_replayed_and_failed_cleanup_keeps_resource_slot(f):
    s = session(f)
    a = send(f, s)
    scope = claim(f, a)
    args = {"command": "printf changed > file", "cwd": ".", "timeoutSeconds": 10}
    with f.services.db.session(write=True) as db:
        db.add(
            AssistantToolCall(
                id="crashed-call",
                run_id=scope.run_id,
                round=1,
                ordinal=0,
                provider_call_id="p",
                name="bash",
                arguments=args,
                argument_digest=call_digest("bash", args),
                status="running",
                review_revision=0,
                attempts=1,
                created_at=f.clock.now(),
            )
        )
        db.flush()
        db.add(
            BashExecution(
                id="crashed-exec",
                tool_call_id="crashed-call",
                session_id=s["id"],
                container_name="test-owned-intent",
                container_id=None,
                state="running",
                worker_owner=scope.owner,
                worker_fence=scope.fence,
                created_at=f.clock.now(),
            )
        )
    f.clock.advance(3)
    next_worker = Scheduler(f.services)
    assert next_worker.lead()
    assert get_run(f, s, a)["errorCode"] == "TOOL_EXECUTION_UNKNOWN"
    f.services.bash.settings = replace(f.settings, bash_enabled=True)
    removed = []

    async def unavailable(name, identity):
        removed.append((name, identity))
        return False

    f.services.bash.remove = unavailable
    asyncio.run(f.services.bash.reconcile(next_worker.owner, next_worker.fence))
    assert f.services.bash.busy_slots() == {scope.run_id}
    assert next_worker.pool.available() == 1
    with f.services.db.session() as db:
        assert db.get(ConversationSession, s["id"]).workspace_unconfirmed
        assert db.get(BashExecution, "crashed-exec").state == "unknown"

    async def recovered(*_):
        return True

    f.services.bash.remove = recovered
    asyncio.run(f.services.bash.reconcile(next_worker.owner, next_worker.fence))
    assert not f.services.bash.busy_slots()
    assert get_run(f, s, a)["status"] == "interrupted"
    assert removed == [("test-owned-intent", "crashed-exec")]


def test_old_worker_shutdown_never_reaps_new_owner_containers(f):
    s = session(f)
    a = send(f, s)
    scope = claim(f, a)
    with f.services.db.session(write=True) as db:
        db.add(
            AssistantToolCall(
                id="new-owner-call",
                run_id=scope.run_id,
                round=1,
                ordinal=0,
                provider_call_id="p",
                name="bash",
                arguments={},
                argument_digest="x",
                status="running",
                review_revision=0,
                attempts=1,
                created_at=f.clock.now(),
            )
        )
        db.flush()
        db.add(
            BashExecution(
                id="new-exec",
                tool_call_id="new-owner-call",
                session_id=s["id"],
                container_name="must-not-delete",
                state="running",
                worker_owner="another-worker",
                worker_fence=42,
                created_at=f.clock.now(),
            )
        )
    f.services.bash.settings = replace(f.settings, bash_enabled=True)

    async def forbidden(*_):
        raise AssertionError("Old worker attempted to reap another owner")

    f.services.bash.remove = forbidden
    asyncio.run(f.worker.close())
    with f.services.db.session() as db:
        assert db.get(BashExecution, "new-exec").state == "running"


@pytest.mark.parametrize(
    "missing", ["MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsQuota", "SecurityOptions"]
)
def test_runner_refuses_unsupported_isolation_instead_of_downgrading(f, missing):
    import json

    runner = f.services.bash
    runner.executable = "/synthetic/docker"
    data = {k: True for k in ("MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsQuota")}
    data["SecurityOptions"] = ["name=seccomp,profile=builtin"]
    data.pop(missing)

    async def info(*args, **kwargs):
        assert args[0] == "info"
        return 0, json.dumps(data).encode(), b""

    runner.cli = info
    asyncio.run(runner.probe(force=True))
    assert not runner.available


def test_created_container_or_cli_transport_failure_cannot_be_reported_as_success(f):
    import json

    from app.core.errors import AppError

    runner = f.services.bash
    runner.available = True
    s = session(f)
    a = send(f, s)
    scope = claim(f, a)
    args = BashArguments(command="printf must-not-count-as-success")
    with f.services.db.session(write=True) as db:
        db.add(
            AssistantToolCall(
                id="transport-call",
                run_id=scope.run_id,
                round=1,
                ordinal=0,
                provider_call_id="p",
                name="bash",
                arguments=args.model_dump(by_alias=True),
                argument_digest=call_digest("bash", args.model_dump(by_alias=True)),
                status="approved",
                review_revision=0,
                attempts=1,
                created_at=f.clock.now(),
            )
        )

    async def cli(*argv, **_):
        if argv[:2] == ("volume", "create"):
            return 0, b"volume", b""
        if argv[:2] == ("volume", "inspect"):
            return (
                0,
                json.dumps({"tavo.instance": runner.instance, "tavo.session": s["id"]}).encode(),
                b"",
            )
        if argv[0] == "create":
            return 0, b"a" * 64, b""
        if argv[0] == "start":
            return 1, b"", b""
        if argv[0] == "inspect":
            return (
                0,
                json.dumps(
                    {
                        "Status": "created",
                        "Running": False,
                        "FinishedAt": "0001-01-01T00:00:00Z",
                        "ExitCode": 0,
                        "Error": "",
                    }
                ).encode(),
                b"",
            )
        raise AssertionError("Unexpected Docker operation")

    async def clean(*_):
        return True

    runner.cli = cli
    runner.remove = clean
    with pytest.raises(AppError, match="TOOL_EXECUTION_UNKNOWN"):
        asyncio.run(runner.execute(scope, "transport-call", args))
    with f.services.db.session() as db:
        assert db.get(ConversationSession, s["id"]).workspace_unconfirmed


def test_server_bash_default_and_operator_override(tmp_path, monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("TAVO_BASH_ENABLED", raising=False)
    monkeypatch.setenv("TAVO_DATA_DIR", str(tmp_path))
    assert Settings.from_env().bash_enabled is True
    monkeypatch.setenv("TAVO_BASH_ENABLED", "0")
    assert Settings.from_env().bash_enabled is False
