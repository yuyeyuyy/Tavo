import asyncio
import hashlib
import json
import os

import pytest
from sqlalchemy import select

from app.agent.mock import MockAssistantModel
from app.llm.turns import AssistantTurn, proposal
from app.models.entities import SandboxInstance, SandboxOperation, WorkspaceCheckpoint
from tests.test_bash_sandbox import result
from tests.test_bash_sandbox import sandbox as original_sandbox
from tests.test_sessions import drive, get_run, send

pytestmark = pytest.mark.skipif(
    os.environ.get("TAVO_TEST_DOCKER") != "1", reason="Explicit real development sandbox tests"
)


@pytest.fixture(name="sandbox")
def sandbox_fixture(f):
    yield from original_sandbox.__wrapped__(f)


class Sequence(MockAssistantModel):
    def __init__(self, calls):
        self.calls = iter(calls)
        self.results = []

    async def turn(self, profile, history, specs, credential=None):
        if history[-1].get("role") == "tool":
            self.results.append(json.loads(history[-1]["content"]))
        try:
            name, args = next(self.calls)
        except StopIteration:
            return await super().turn(profile, history, specs, credential)
        call = proposal("sequence-" + str(len(self.results)), name, args)
        native = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.provider_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        }
        return AssistantTurn(calls=[call], native=native)


def test_real_python_generate_repair_and_reuse_same_sandbox(sandbox):
    f, create = sandbox
    initial = "print(1/0)\n"
    fixed = "print(6*7)\n"
    model = Sequence(
        [
            ("workspace_write", {"path": "task.py", "content": initial}),
            ("sandbox", {"command": "python task.py"}),
            (
                "workspace_write",
                {
                    "path": "task.py",
                    "content": fixed,
                    "expectedSha256": hashlib.sha256(initial.encode()).hexdigest(),
                },
            ),
            ("sandbox", {"command": "python task.py"}),
        ]
    )
    f.services.assistant.mock = model
    s = create()
    accepted = send(f, s, "Generate, execute and repair this local script.")
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded" and run["toolCount"] == 4, run
    assert model.results[1]["errorCode"] == "EXECUTION_ERROR"
    assert "ZeroDivisionError" in model.results[1]["output"]["stderr"]
    assert result(f, s)["output"]["stdout"] == "42\n"
    with f.services.db.session() as db:
        ids = list(db.scalars(select(SandboxOperation.sandbox_id)))
        assert len(set(ids)) == 1
        assert all(row.state == "closed" for row in db.scalars(select(SandboxInstance)))


def test_real_long_command_and_oversize_error_can_be_fixed(sandbox):
    f, create = sandbox
    model = Sequence(
        [
            ("sandbox", {"command": "printf long-ok #" + ("x" * 5000)}),
            ("sandbox", {"command": "#" + ("x" * 32768)}),
            ("sandbox", {"command": "printf fixed-ok"}),
        ]
    )
    f.services.assistant.mock = model
    s = create()
    accepted = send(f, s, "Exercise large command validation and repair.")
    drive(f)
    assert get_run(f, s, accepted)["status"] == "succeeded"
    assert model.results[0]["output"]["stdout"] == "long-ok"
    assert model.results[1]["errorCode"] == "COMMAND_TOO_LONG"
    assert model.results[1]["output"]["sandboxId"] is None
    assert result(f, s)["output"]["stdout"] == "fixed-ok"


def test_real_isolation_base_packages_and_pid_quota(sandbox):
    f, create = sandbox
    code = """python - <<'PY'
import os, socket, pathlib
import pip,pytest,requests,httpx,pydantic
assert not pathlib.Path('/'+'Users/macbook').exists()
assert not pathlib.Path('/var/run/'+'docker.sock').exists()
assert os.getuid()==10001
try:
 socket.create_connection(('1.1.1.1',443),.2)
 raise AssertionError('Direct Internet route exists')
except OSError: pass
try:
 pathlib.Path('/etc/test-write').write_text('no')
 raise AssertionError('Root filesystem writable')
except OSError: pass
print('isolation-ok')
PY"""
    s = create()
    send(f, s, "bash: " + code)
    drive(f)
    assert result(f, s)["output"]["stdout"] == "isolation-ok\n", result(f, s)
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    script = """python - <<'PY'
import subprocess
children=[]
try:
 for _ in range(256): children.append(subprocess.Popen(['sleep','1']))
except OSError: pass
finally:
 for child in children: child.wait()
PY"""
    send(f, s, "bash: " + script, "pids")
    drive(f)
    assert result(f, s)["errorCode"] == "RESOURCE_LIMIT", result(f, s)


def test_real_unapproved_bulk_delete_does_not_replace_checkpoint_or_rerun_script(sandbox):
    f, create = sandbox
    s = create()
    send(
        f,
        s,
        "bash: python -c \"from pathlib import Path;[(Path(str(i)).write_text('old')) for i in range(12)]\"",
    )
    drive(f)
    with f.services.db.session() as db:
        old = db.get(WorkspaceCheckpoint, s["id"]).revision
    s = f.client.get("/api/v1/sessions/" + s["id"]).json()
    accepted = send(
        f,
        s,
        "bash: python -c \"from pathlib import Path;[p.unlink() for p in Path('.').iterdir() if p.is_file()]\"",
        "delete",
    )
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "waiting_review", run
    call = run["toolCalls"][0]
    with f.services.db.session() as db:
        assert db.get(WorkspaceCheckpoint, s["id"]).revision == old
    preview = f.client.get(f"/api/v1/sessions/{s['id']}/tool-calls/{call['id']}/approval")
    assert preview.status_code == 200 and preview.json()["details"]["deleted"] == 12
    approved = f.client.post(
        f"/api/v1/sessions/{s['id']}/tool-calls/{call['id']}/decision",
        json={"action": "approve", "expectedRevision": call["reviewRevision"]},
    )
    assert approved.status_code == 200
    drive(f)
    run = get_run(f, s, accepted)
    assert run["status"] == "succeeded" and run["toolCalls"][0]["attempts"] == 1, run
    with f.services.db.session() as db:
        assert db.get(WorkspaceCheckpoint, s["id"]).revision != old


def test_real_concurrent_runs_have_only_one_execution_sandbox(sandbox):
    f, create = sandbox
    s, other = create(), create()
    send(f, s, "bash: sleep .3; printf first")
    send(f, other, "bash: printf second")

    async def execute():
        await f.worker.tick()
        maximum = 0
        while f.worker.active:
            with f.services.db.session() as db:
                active = list(
                    db.scalars(select(SandboxInstance).where(SandboxInstance.state != "closed"))
                )
                maximum = max(maximum, len(active))
            await asyncio.sleep(0.03)
        assert maximum == 1

    asyncio.run(execute())
    assert result(f, s)["output"]["stdout"] == "first"
    assert result(f, other)["output"]["stdout"] == "second"


def test_expanded_profile_and_downgrade_apply_within_one_run(sandbox):
    f, create = sandbox
    command = "python -c \"from pathlib import Path; print(Path('/sys/fs/cgroup/memory.max').read_text().strip())\""
    model = Sequence(
        [
            ("sandbox", {"command": command, "resourceProfile": "expanded"}),
            ("sandbox", {"command": command, "resourceProfile": "default"}),
        ]
    )
    f.services.assistant.mock = model
    s = create()
    send(f, s, "Check configured resource profiles.")
    drive(f)
    assert model.results[0]["output"]["stdout"] == "1073741824\n"
    assert model.results[1]["output"]["stdout"] == "536870912\n"


def test_proxy_startup_checks_uds_and_cleans_warmup(sandbox):
    from dataclasses import replace

    f, _ = sandbox
    runner = f.services.bash
    policy = runner.settings.sandbox.model_copy(
        update={"network": runner.settings.sandbox.network.model_copy(update={"mode": "proxy"})}
    )
    runner.settings = replace(runner.settings, sandbox=policy)
    runner.manager.policy = policy
    asyncio.run(runner.prepare())
    assert runner.available
    with f.services.db.session() as db:
        assert not list(
            db.scalars(select(SandboxInstance).where(SandboxInstance.state != "closed"))
        )
