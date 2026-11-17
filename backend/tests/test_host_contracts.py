import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.agent.bash_policy import classify
from app.agent.host import HostBashArguments, OutputGuard
from app.agent.tools import ToolRegistry
from app.core.errors import AppError
from app.core.sandbox_config import default_policy
from app.models.entities import AssistantRun, ConversationSession, HostExecution
from app.repositories.database import upgrade_database
from tests.test_agent_protocols import configure, tool_response
from tests.test_agent_recovery import claim
from tests.test_sessions import get_run, send, session


def test_new_default_and_existing_session_capability_do_not_mix(f):
    s = f.client.post("/api/v1/sessions", json={"profileId": "mock"}).json()
    assert s["executionScope"] == "host_and_sandbox"
    scope = claim(f, send(f, s))
    _, c, _, specs = f.services.assistant.context.prepare(scope)
    assert c.policy_version == "5" and "host_bash" in {x.name for x in specs}
    assert "bash" not in c.tool_names
    # Migration-like existing row. A fresh Run must not grant it host capabilities.
    old = session(f, mode="full_access")
    with f.services.db.session(write=True) as db:
        db.get(AssistantRun, scope.run_id).status = "succeeded"
        db.get(ConversationSession, old["id"]).execution_scope = "sandbox_only"
    old_scope = claim(f, send(f, old))
    _, old_context, _, specs = f.services.assistant.context.prepare(old_scope)
    assert old_context.policy_version == "4" and old_context.host_policy is None
    assert "host_bash" not in {x.name for x in specs}
    assert "host_bash" not in old_context.tool_names


@pytest.mark.parametrize("name", ["sandbox", "host_bash"])
@pytest.mark.parametrize(
    "mode,action,review",
    [
        ("full_access", "allow", False),
        ("auto_review", "allow", True),
        ("request_approval", "ask", False),
    ],
)
def test_both_command_targets_enter_the_same_review_pipeline(name, mode, action, review):
    registry = ToolRegistry(bash_enabled=True, host_enabled=True)
    risk = classify(name, {"command": "pwd"}, mode, [name], registry)
    assert risk.action == action and risk.model_review == review
    assert classify(name, {"command": "pwd"}, mode, [], registry).action == "deny"


@pytest.mark.parametrize("decision", ["allow", "deny", "ask"])
def test_host_real_adapter_model_review_is_enforced_before_execution(f, decision):
    profile = configure(
        f,
        "deepseek",
        lambda _: httpx.Response(
            200, json=tool_response("completions", "host_bash", {"command": "docker ps"})
        ),
        review_action=decision,
    )
    s = session(f, profile.id, "auto_review")
    a = send(f, s, "Inspect Docker status")
    scope = claim(f, a)
    with f.services.db.session(write=True) as db:
        db.get(AssistantRun, scope.run_id).profile = profile.model_dump()

    async def review():
        await f.services.assistant.model(scope)
        await f.services.assistant.review(scope)

    asyncio.run(review())
    call = get_run(f, s, a)["toolCalls"][0]
    assert call["attempts"] == 0
    assert call["decisions"][-1]["source"] == "model"
    assert call["decisions"][-1]["action"] == decision
    assert f.services.host.busy_slots() == set()


def test_host_arguments_and_output_guard_check_utf8_and_stream_boundaries():
    registry = ToolRegistry(host_enabled=True)
    registry.validate("host_bash", {"command": "#" * 32768})
    with pytest.raises(AppError, match="COMMAND_TOO_LONG"):
        registry.validate("host_bash", {"command": "x" * 32769})
    with pytest.raises(AppError, match="COMMAND_TOO_LONG"):
        registry.validate("host_bash", {"command": "你" * 10923})
    host = default_policy().host.model_copy(update={"default_timeout_seconds": 12})
    policy = default_policy().model_copy(update={"host": host})
    assert (
        HostBashArguments.model_validate(
            {"command": "pwd"}, context={"policy": policy}
        ).timeout_seconds
        == 12
    )
    guard = OutputGuard(["synthetic-cross-chunk-token"])
    guard.check("stdout", b"synthetic-cross-")
    assert not guard.denied
    guard.check("stdout", b"chunk-token")
    assert guard.denied


def test_recovery_never_kills_a_reused_pid_and_retains_unknown_slot(f, monkeypatch):
    s = session(f)
    a = send(f, s)
    scope = claim(f, a)
    asyncio.run(f.services.assistant.model(scope))
    with f.services.db.session(write=True) as db:
        run = db.get(AssistantRun, scope.run_id)
        from app.models.entities import AssistantToolCall

        call = db.scalar(select(AssistantToolCall).where(AssistantToolCall.run_id == run.id))
        db.add(
            HostExecution(
                id="host-recovery",
                tool_call_id=call.id,
                run_id=run.id,
                slot=1,
                state="unknown",
                worker_owner="past-worker",
                worker_fence=0,
                pid=987654,
                created_at=f.clock.now(),
            )
        )
    runner = f.services.host

    async def not_owned(_):
        return False

    async def still_exists(_):
        return False

    monkeypatch.setattr(runner, "owns", not_owned)
    monkeypatch.setattr(runner, "group_absent", still_exists)
    monkeypatch.setattr(
        "app.agent.host.os.kill", lambda *_: pytest.fail("must not kill an unrelated PID")
    )
    asyncio.run(runner.reconcile(scope.owner, scope.fence))
    assert scope.run_id in runner.busy_slots()


def test_0008_migration_keeps_existing_sessions_sandbox_only(tmp_path):
    path = tmp_path / "upgrade.sqlite"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", "sqlite:///" + str(path))
    command.upgrade(config, "0007_sandbox_execution_policy")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO conversation_sessions(id,title,profile_id,review_mode,bash_enabled,revision,next_sequence,created_at,updated_at,workspace_unconfirmed) VALUES('old-session','old','mock','full_access',1,0,1,'before','before',0)"
        )
    upgrade_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT title,execution_scope FROM conversation_sessions").fetchall() == [
            ("old", "sandbox_only")
        ]
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0008_host_bash",
        )
        assert db.execute("SELECT count(*) FROM host_executions").fetchone() == (0,)


def test_detached_output_does_not_silently_release_capacity(f, monkeypatch):
    s = session(f)
    scope = claim(f, send(f, s))
    asyncio.run(f.services.assistant.model(scope))
    with f.services.db.session(write=True) as db:
        from app.models.entities import AssistantToolCall

        call = db.scalar(select(AssistantToolCall).where(AssistantToolCall.run_id == scope.run_id))
        db.add(
            HostExecution(
                id="detached",
                tool_call_id=call.id,
                run_id=scope.run_id,
                slot=1,
                state="unknown_detached",
                worker_owner="past",
                worker_fence=0,
                pid=987654,
                created_at=f.clock.now(),
            )
        )
    runner = f.services.host

    async def absent(_):
        return True

    monkeypatch.setattr(runner, "group_absent", absent)
    asyncio.run(runner.reconcile(scope.owner, scope.fence))
    assert runner.busy_slots() == {scope.run_id}
