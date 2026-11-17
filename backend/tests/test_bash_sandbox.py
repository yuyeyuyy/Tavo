import asyncio
import os
import shutil
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.agent.tools import ToolRegistry
from app.models.entities import (
    BashExecution,
    ConversationSession,
    SandboxInstance,
    SandboxOperation,
)
from tests.test_sessions import drive, get_run, send

pytestmark = pytest.mark.skipif(
    os.environ.get("TAVO_TEST_DOCKER") != "1",
    reason="Explicit real Docker integration suite; run TAVO_TEST_DOCKER=1",
)


@pytest.fixture
def sandbox(f):
    settings = replace(f.settings, bash_enabled=True)
    f.services.settings = settings
    f.services.bash.settings = settings
    f.services.bash.executable = shutil.which("docker")
    registry = ToolRegistry(bash_enabled=True)
    f.services.assistant.registry = registry
    f.services.assistant.context.registry = registry
    f.services.assistant.reviewer.registry = registry
    asyncio.run(f.services.bash.prepare())
    assert f.services.bash.available, "Build backend/sandbox/Dockerfile and start Docker first"
    ids = []

    def create(mode="auto_sandbox"):
        r = f.client.post("/api/v1/sessions", json={"profileId": "mock", "reviewMode": mode})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
        return r.json()

    yield f, create

    async def cleanup():
        # Only volumes owned by this fixture's isolated database and explicit sessions.
        import re

        rc, names, _ = await f.services.bash.cli(
            "volume",
            "ls",
            "--filter",
            "label=tavo.instance=" + f.services.bash.instance,
            "--format",
            "{{.Name}}",
        )
        assert rc == 0
        for name in names.decode().splitlines():
            assert re.fullmatch(
                r"tavo-(?:bash|work)-"
                + f.services.bash.instance
                + r"-[a-f0-9]{24}|tavo-egress-"
                + f.services.bash.instance
                + r"-[a-f0-9]{32}",
                name,
            )
            await f.services.bash.cli("volume", "rm", name)

    asyncio.run(cleanup())


def messages(f, s):
    return f.client.get(f"/api/v1/sessions/{s['id']}/messages").json()["items"]


def result(f, s):
    return [m["result"] for m in messages(f, s) if m["kind"] == "tool_result"][-1]


def test_real_bash_write_read_persistence_and_session_isolation(sandbox):
    f, create = sandbox
    s = create()
    a = send(f, s, 'bash: printf hello > greeting; cat greeting; printf " uid="; id -u')
    drive(f)
    run = get_run(f, s, a)
    assert run["status"] == "succeeded", run
    assert result(f, s)["output"]["stdout"] == "hello uid=10001\n"
    assert run["toolCalls"][0]["decisions"][-1]["source"] == "policy"
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    a = send(f, s, "bash: cat greeting", "second")
    drive(f)
    assert result(f, s)["output"]["stdout"] == "hello"
    other = create()
    a = send(f, other, "bash: test ! -e greeting; printf isolated")
    drive(f)
    assert result(f, other)["output"]["stdout"] == "isolated"
    with f.services.db.session() as db:
        assert all(r.state == "finished" for r in db.scalars(select(BashExecution)))
    assert not f.services.bash.busy_slots()


def test_real_bash_exit_failure_output_bound_and_no_host_access(sandbox):
    f, create = sandbox
    s = create()
    send(
        f,
        s,
        "bash: printf failure >&2; exit 7",
    )
    drive(f)
    r = result(f, s)
    assert (
        r["status"] == "failed"
        and r["output"]["exitCode"] == 7
        and r["output"]["stderr"] == "failure"
    )
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    send(f, s, "bash: python -c 'import sys;sys.stdout.write(\"x\"*70000)'", "large")
    drive(f)
    r = result(f, s)
    assert r["status"] == "succeeded" and r["errorCode"] is None
    assert r["output"]["truncated"] is True


def test_real_bash_ask_then_cancel_stops_container_and_unknown_requires_confirmation(sandbox):
    f, create = sandbox
    s = create("ask")
    a = send(f, s, "bash: sleep 20 & wait")
    drive(f)
    call = get_run(f, s, a)["toolCalls"][0]
    assert call["status"] == "waiting_review" and call["attempts"] == 0
    f.client.post(
        f"/api/v1/sessions/{s['id']}/tool-calls/{call['id']}/decision",
        json={"action": "approve", "expectedRevision": call["reviewRevision"]},
    )

    async def cancel():
        await f.worker.tick()
        for _ in range(100):
            with f.services.db.session() as db:
                row = db.scalar(select(SandboxOperation))
                instance = db.get(SandboxInstance, row.sandbox_id) if row else None
                ready = row and row.state == "running"
            if ready:
                rc, processes, _ = await f.services.bash.cli(
                    "top", instance.container_name, "-eo", "pid,comm"
                )
                ready = rc == 0 and b"sleep" in processes
            if ready:
                break
            await asyncio.sleep(0.05)
        assert ready, {
            "runStatus": get_run(f, s, a)["status"],
            "runError": get_run(f, s, a)["errorCode"],
            "top": processes.decode() if instance else None,
        }
        f.client.post(f"/api/v1/sessions/{s['id']}/runs/{a['run']['id']}/cancel")
        for task in list(f.worker.active.values()):
            task.cancel()
        await asyncio.gather(*list(f.worker.active.values()), return_exceptions=True)
        await f.services.bash.reconcile(f.worker.owner, f.worker.fence)

    asyncio.run(cancel())
    assert get_run(f, s, a)["status"] == "cancelled"
    cancelled = result(f, s)["output"]
    assert cancelled["sandboxId"] and cancelled["durationMs"] > 0
    assert cancelled["errorCode"] == "CANCELLED"
    assert not f.services.bash.busy_slots()
    with f.services.db.session() as db:
        assert db.get(ConversationSession, s["id"]).workspace_unconfirmed
    r = f.client.post(f"/api/v1/sessions/{s['id']}/runs/{a['run']['id']}/retry")
    assert r.status_code == 202
    drive(f)
    assert (
        f.client.get(f"/api/v1/sessions/{s['id']}/runs/{r.json()['id']}").json()["status"]
        == "waiting_review"
    )


def test_real_bash_timeout_and_resource_boundaries(sandbox):
    from app.agent.mock import MockAssistantModel

    f, create = sandbox
    s = create()
    script = """python - <<'CHECK'
import os, socket, resource
assert os.getuid()==10001
assert resource.getrlimit(resource.RLIMIT_FSIZE)==(67108864,67108864)
assert os.environ['HOME']=='/tmp'
assert not any(line.split()[1]=='00000000' for line in open('/proc/net/route').readlines()[1:])
try:
 socket.create_connection(('198.51.100.1',443),.2)
 raise AssertionError('External network reachable')
except OSError: pass
assert 'NoNewPrivs:\\t1' in open('/proc/self/status').read()
assert 'CapEff:\\t0000000000000000' in open('/proc/self/status').read()
assert open('/sys/fs/cgroup/memory.max').read().strip()=='536870912'
try:
 open('/forbidden','w')
 raise AssertionError('Root filesystem writable')
except OSError: pass
print('limits-ok')
CHECK"""
    send(f, s, "bash: " + script)
    drive(f)
    assert result(f, s)["output"]["stdout"] == "limits-ok\n", result(f, s)

    class Timed(MockAssistantModel):
        async def turn(self, *args):
            import json

            turn = await super().turn(*args)
            if turn.calls:
                turn.calls[0].arguments["timeoutSeconds"] = 1
                turn.native["tool_calls"][0]["function"]["arguments"] = json.dumps(
                    turn.calls[0].arguments
                )
            return turn

    f.services.assistant.mock = Timed()
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    send(f, s, "bash: sleep 20 & wait", "timeout")
    drive(f)
    r = result(f, s)
    assert r["status"] == "failed" and r["errorCode"] == "TIMEOUT" and r["output"]["timedOut"]
    assert not f.services.bash.busy_slots()


def test_real_bash_binary_output_is_bounded_and_oom_is_a_failure(sandbox):
    f, create = sandbox
    s = create()
    send(f, s, "bash: python -c 'import sys;sys.stdout.buffer.write(b\"\\xff\\x00\"*3000)' ")
    drive(f)
    r = result(f, s)
    import json

    assert r["status"] == "succeeded" and r["output"]["truncated"]
    assert len(json.dumps(r["output"], ensure_ascii=False).encode()) < 8192
    other = create()
    send(f, other, "bash: python -c 'x=bytearray(800*1024*1024)' ")
    drive(f)
    r = result(f, other)
    assert r["status"] == "failed" and r["output"]["exitCode"] == 137


def test_real_bash_cwd_cannot_follow_workspace_symlink_outside(sandbox):
    import json

    from app.agent.mock import MockAssistantModel

    f, create = sandbox
    s = create()
    send(f, s, "bash: ln -s /tmp escape")
    drive(f)

    class Linked(MockAssistantModel):
        async def turn(self, *args):
            turn = await super().turn(*args)
            if turn.calls:
                turn.calls[0].arguments["cwd"] = "escape"
                turn.native["tool_calls"][0]["function"]["arguments"] = json.dumps(
                    turn.calls[0].arguments
                )
            return turn

    f.services.assistant.mock = Linked()
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    send(f, s, "bash: printf must-not-run", "symlink")
    drive(f)
    r = result(f, s)
    assert r["status"] == "failed" and r["output"]["exitCode"] == 126
    assert "must-not-run" not in r["output"]["stdout"]
