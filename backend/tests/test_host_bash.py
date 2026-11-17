import asyncio
import json
import os
from dataclasses import replace

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.agent.tools import ToolRegistry
from app.models.entities import HostExecution
from tests.test_sandbox_development_docker import Sequence
from tests.test_sessions import drive, get_run, send, session

pytestmark = pytest.mark.skipif(
    os.environ.get("TAVO_TEST_HOST") != "1",
    reason="Explicit real host process tests in temporary projects",
)


@pytest.fixture
def host(f, tmp_path_factory):
    work = tmp_path_factory.mktemp("host-project")
    policy = f.settings.sandbox.model_copy(
        update={"host": f.settings.sandbox.host.model_copy(update={"default_cwd": str(work)})}
    )
    settings = replace(f.settings, sandbox=policy)
    f.services.host.policy = policy.host
    f.services.host.settings = settings
    registry = ToolRegistry(bash_enabled=False, host_enabled=True, policy=policy)
    f.services.assistant.registry = registry
    f.services.assistant.context.registry = registry
    f.services.assistant.reviewer.registry = registry
    return f, work


def output(f, s):
    messages = f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()["items"]
    return [m["result"] for m in messages if m["kind"] == "tool_result"][-1]


@pytest.mark.parametrize("mode", ["full_access", "auto_review", "request_approval"])
def test_real_host_modes_and_real_files_without_docker(host, mode):
    f, work = host
    s = session(f, mode=mode)
    a = send(f, s, "host: printf host-ok > note; cat note")
    drive(f)
    run = get_run(f, s, a)
    assert run["context"]["policyVersion"] == "5"
    assert "host_bash" in run["context"]["toolNames"] and "bash" not in run["context"]["toolNames"]
    if mode == "request_approval":
        assert run["status"] == "waiting_review" and not (work / "note").exists()
        call = run["toolCalls"][0]
        response = f.client.post(
            f"/api/v1/sessions/{s['id']}/tool-calls/{call['id']}/decision",
            json={"action": "approve", "expectedRevision": call["reviewRevision"]},
        )
        assert response.status_code == 200
        drive(f)
    assert (work / "note").read_text() == "host-ok"
    result = output(f, s)
    assert result["status"] == "succeeded", result
    assert result["output"]["stdout"] == "host-ok" and result["output"]["executionTarget"] == "host"
    assert result["output"]["sandboxId"] is None
    assert not f.services.host.busy_slots()


def test_real_host_exception_repair_exit_codes_and_environment(host, monkeypatch):
    f, work = host
    monkeypatch.setenv("TAVO_FIXTURE_TOKEN", "synthetic-not-for-child")
    model = Sequence(
        [
            ("host_bash", {"command": "printf 'exit 7' > task.sh; bash task.sh"}),
            (
                "host_bash",
                {
                    "command": "test -z \"$TAVO_FIXTURE_TOKEN\" && printf 'printf repaired' > task.sh && bash task.sh"
                },
            ),
        ]
    )
    f.services.assistant.mock = model
    s = session(f, mode="full_access")
    a = send(f, s, "Repair the temporary script")
    drive(f)
    assert model.results[0]["output"]["exitCode"] == 7
    assert model.results[0]["errorCode"] == "EXECUTION_ERROR"
    assert output(f, s)["output"]["stdout"] == "repaired"
    assert get_run(f, s, a)["toolCount"] == 2


def test_real_host_timeout_cleans_group(host):
    f, _ = host
    f.services.assistant.mock = Sequence(
        [("host_bash", {"command": "sleep 30 & wait", "timeoutSeconds": 1})]
    )
    s = session(f, mode="full_access")
    send(f, s, "Timeout a temporary child")
    drive(f)
    result = output(f, s)
    assert result["errorCode"] == "TIMEOUT", result
    assert result["output"]["timedOut"]
    with f.services.db.session() as db:
        row = db.scalar(select(HostExecution))
        assert row.state == "finished"
        assert asyncio.run(f.services.host.group_absent(row.pid))


@pytest.mark.parametrize(
    "command,cwd,error",
    [("pwd", "missing-subdirectory", "INVALID_INPUT"), ("pwd", "protected", "POLICY_DENIED")],
)
def test_bad_or_protected_cwd_never_starts_a_process(host, command, cwd, error):
    f, _ = host
    directory = str(f.settings.data_dir) if cwd == "protected" else cwd
    f.services.assistant.mock = Sequence([("host_bash", {"command": command, "cwd": directory})])
    s = session(f, mode="full_access")
    send(f, s, "Validate cwd")
    drive(f)
    assert output(f, s)["errorCode"] == error
    with f.services.db.session() as db:
        assert not list(db.scalars(select(HostExecution)))


def test_host_output_is_bounded_and_marks_truncation(host):
    f, _ = host
    s = session(f, mode="full_access")
    send(f, s, "host: python3 -c \"print('x'*1000000)\"")
    drive(f)
    result = output(f, s)["output"]
    assert result["success"] and result["truncated"]
    assert len(result["stdout"].encode()) <= f.services.host.policy.public_output_bytes


def test_repeated_failed_host_commands_stop_before_reexecution(host):
    f, _ = host
    f.services.assistant.mock = Sequence([("host_bash", {"command": "exit 7"})] * 4)
    s = session(f, mode="full_access")
    a = send(f, s, "Do not repeat this failure indefinitely")
    drive(f)
    run = get_run(f, s, a)
    assert run["errorCode"] == "TOOL_LOOP_DETECTED", run
    with f.services.db.session() as db:
        assert len(list(db.scalars(select(HostExecution)))) == 2


def test_known_credentials_do_not_reach_results_or_history(host):
    f, work = host
    value = "synthetic-host-output-secret"
    with f.services.db.session(write=True) as db:
        f.services.secrets.put(db, "test/host-secret", SecretStr(value))
    (work / "fixture-secret").write_text("x" * 100000 + value)
    s = session(f, mode="full_access")
    a = send(f, s, "host: cat fixture-secret")
    drive(f)
    result = output(f, s)
    assert result["errorCode"] == "POLICY_DENIED", result
    assert result["output"]["stdout"] == ""
    page = f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()
    assert value not in json.dumps(page) and value not in json.dumps(get_run(f, s, a))


def test_cancelled_run_cleans_its_owned_process_group(host):
    f, _ = host
    from tests.test_agent_recovery import claim

    s = session(f, mode="full_access")
    a = send(f, s, "host: sleep 30 & wait")
    scope = claim(f, a)

    async def exercise():
        await f.services.assistant.model(scope)
        await f.services.assistant.review(scope)
        task = asyncio.create_task(f.services.assistant.tools(scope))
        for _ in range(200):
            if f.services.host.active:
                break
            await asyncio.sleep(0.01)
        assert f.services.host.active
        response = f.client.post(f"/api/v1/sessions/{s['id']}/runs/{scope.run_id}/cancel")
        assert response.status_code == 200
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with f.services.db.session() as db:
            row = db.scalar(select(HostExecution))
            assert row.state == "finished"
            assert await f.services.host.group_absent(row.pid)
        assert not f.services.host.busy_slots()

    asyncio.run(exercise())
    assert get_run(f, s, a)["status"] == "cancelled"
    assert output(f, s)["errorCode"] == "CANCELLED"


def test_single_host_slot_serializes_different_runs(host):
    f, work = host
    from tests.test_agent_recovery import claim

    first, second = session(f, mode="full_access"), session(f, mode="full_access")
    a = send(f, first, "host: printf first > first; sleep .3; printf done")
    b = send(f, second, "host: test -f first; printf second > second; printf done")
    scopes = [claim(f, a), claim(f, b)]

    async def exercise():
        for scope in scopes:
            await f.services.assistant.model(scope)
            await f.services.assistant.review(scope)
        tasks = [asyncio.create_task(f.services.assistant.tools(scopes[0]))]
        for _ in range(200):
            if (work / "first").exists():
                break
            await asyncio.sleep(0.01)
        assert (work / "first").exists()
        tasks.append(asyncio.create_task(f.services.assistant.tools(scopes[1])))
        while any(not task.done() for task in tasks):
            with f.services.db.session() as db:
                assert (
                    len(
                        list(
                            db.scalars(
                                select(HostExecution).where(HostExecution.state != "finished")
                            )
                        )
                    )
                    <= 1
                )
            await asyncio.sleep(0.01)
        await asyncio.gather(*tasks)

    asyncio.run(exercise())
    assert (work / "second").read_text() == "second"
    assert not f.services.host.busy_slots()


def test_supervisor_parent_pipe_loss_cleans_shell_and_children(host):
    f, work = host
    import sys
    import tempfile
    from uuid import uuid4

    from app.agent.host import SUPERVISOR

    async def exercise():
        with tempfile.TemporaryFile() as result:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(SUPERVISOR),
                str(uuid4()),
                str(result.fileno()),
                "0.1",
                "0.5",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                pass_fds=(result.fileno(),),
                start_new_session=True,
            )
            try:
                proc.stdin.write(
                    json.dumps(
                        {
                            "command": "printf started > parent-loss; sleep 30 & wait",
                            "cwd": str(work),
                            "environment": {"PATH": os.defpath},
                        }
                    ).encode()
                    + b"\n"
                )
                for _ in range(100):
                    proc.stdin.write(b"alive\n")
                    await proc.stdin.drain()
                    if (work / "parent-loss").exists():
                        break
                    await asyncio.sleep(0.01)
                assert (work / "parent-loss").exists()
                proc.stdin.close()  # Same EOF signal produced by an exiting/killed Worker.
                await asyncio.wait_for(proc.wait(), 3)
                assert await f.services.host.group_absent(proc.pid)
            finally:
                if proc.returncode is None:
                    os.killpg(proc.pid, 9)
                    await proc.wait()

    asyncio.run(exercise())


def test_credentials_added_during_execution_are_checked_before_publication(host):
    f, work = host
    from tests.test_agent_recovery import claim

    value = "synthetic-new-during-host-execution"
    (work / "late-secret").write_text(value)
    s = session(f, mode="full_access")
    a = send(f, s, "host: sleep .5; cat late-secret")
    scope = claim(f, a)

    async def exercise():
        await f.services.assistant.model(scope)
        await f.services.assistant.review(scope)
        task = asyncio.create_task(f.services.assistant.tools(scope))
        for _ in range(200):
            if f.services.host.active:
                break
            await asyncio.sleep(0.01)
        assert f.services.host.active
        with f.services.db.session(write=True) as db:
            f.services.secrets.put(db, "test/new-during-command", SecretStr(value))
        await task

    asyncio.run(exercise())
    result = output(f, s)
    assert result["errorCode"] == "POLICY_DENIED", result
    assert value not in json.dumps(result)
