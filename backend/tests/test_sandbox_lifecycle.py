import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.agent.api_credentials import ApiKeyHandler, ApiKeys
from app.core.sandbox_config import NetworkTarget, SandboxPolicy
from app.models.entities import AssistantRun, SandboxInstance
from tests.test_agent_recovery import claim
from tests.test_secret_submissions import KEY, sealed
from tests.test_sessions import send, session


def instance(f, scope, key, **values):
    with f.services.db.session(write=True) as db:
        row = SandboxInstance(
            id=key,
            slot=1,
            run_id=scope.run_id,
            session_id=scope.session_id,
            container_name="test-" + key,
            state="closed",
            worker_owner=scope.owner,
            worker_fence=scope.fence,
            resource_profile="default",
            created_at=f.clock.now(),
            last_used_at=f.clock.now(),
            **values,
        )
        db.add(row)
    return row


@pytest.mark.parametrize(
    "list_rc,names,expected", [(0, b"", True), (0, b"socket\n", False), (1, b"", False)]
)
def test_repeated_socket_volume_cleanup_requires_proven_absence(f, list_rc, names, expected):
    s = session(f)
    scope = claim(f, send(f, s))
    row = instance(f, scope, "cleanup", socket_volume="socket")
    manager = f.services.bash.manager
    manager.runner.remove = AsyncMock(return_value=True)

    async def cli(*args, **kwargs):
        if args[0] == "ps":
            return 0, b"", b""
        if args[:2] == ("volume", "rm"):
            return 1, b"", b""
        assert args[:2] == ("volume", "ls")
        return list_rc, names, b""

    manager.runner.cli = cli
    assert asyncio.run(manager.close_instance(row)) is expected


def test_api_key_submission_is_scoped_cas_and_survives_rotation(f):
    target = NetworkTarget(
        id="api-test",
        hostname="api.example.test",
        paths=["/v1"],
        capability="api_test",
        credential=True,
    )
    policy = f.settings.sandbox.model_copy(
        update={"network": f.settings.sandbox.network.model_copy(update={"targets": [target]})}
    )
    keys = ApiKeys(f.services.secrets, policy)
    handler = ApiKeyHandler(keys)
    f.services.secret_submissions.handlers["sandbox.api_key"] = handler
    f.services.bash.manager.policy = policy
    f.services.bash.manager.egress.keys = keys
    value = f.client.post(
        "/api/v1/secret-submissions/challenges",
        json={"purpose": "sandbox.api_key", "resourceId": target.id, "expectedRevision": 0},
    )
    assert value.status_code == 200
    challenge = value.json()
    bad = f.client.post(
        "/api/v1/secret-submissions",
        json=sealed(challenge, header_changes={"purpose": "model.api_key"}),
    )
    assert bad.status_code == 422
    challenge = f.client.post(
        "/api/v1/secret-submissions/challenges",
        json={"purpose": "sandbox.api_key", "resourceId": target.id, "expectedRevision": 0},
    ).json()
    result = f.client.post("/api/v1/secret-submissions", json=sealed(challenge))
    assert result.status_code == 200 and result.json()["credentialRevision"] == 1
    assert keys.get(target).get_secret_value() == KEY
    stale = f.client.post(
        "/api/v1/secret-submissions/challenges",
        json={"purpose": "sandbox.api_key", "resourceId": target.id, "expectedRevision": 0},
    )
    assert stale.status_code == 409
    f.services.secrets.rotate()
    assert keys.get(target).get_secret_value() == KEY
    changed = target.model_copy(update={"hostname": "other.example.test"})
    assert not keys.ready(changed)
    assert KEY not in json.dumps(f.client.get("/api/v1/sandbox-network-targets").json())


def test_network_quota_reserved_until_final_usage_proven_and_frozen_capabilities(f):
    s = session(f)
    accepted = send(f, s)
    scope = claim(f, accepted)
    manager = f.services.bash.manager
    target = NetworkTarget(id="public", hostname="example.test", paths=["/"])
    data = manager.policy.model_dump()
    data["network"].update(mode="proxy", targets=[target.model_dump()], requests_per_run=3)
    manager.policy = SandboxPolicy.model_validate(data)
    with f.services.db.session(write=True) as db:
        db.get(AssistantRun, scope.run_id).context = {
            "sandboxPolicy": manager.policy.model_dump(),
            "policyVersion": "3",
        }
    first = instance(f, scope, "first")
    config = manager.egress.configuration(scope.run_id)
    manager.egress.reserve(first, config)
    assert manager.egress.configuration(scope.run_id)["usage"]["count"] == 3
    manager.egress.store_usage(first, {"count": 1, "timestamps": [1.0]}, release=True)
    second = instance(f, scope, "second")
    config = manager.egress.configuration(scope.run_id)
    assert config["usage"]["count"] == 1
    manager.egress.reserve(second, config)
    manager.egress.store_usage(second, {"count": 2, "timestamps": [2.0]})
    assert manager.egress.configuration(scope.run_id)["usage"]["count"] == 3
    manager.egress.store_usage(second, {"count": 2, "timestamps": [2.0]}, release=True)
    assert manager.egress.configuration(scope.run_id)["usage"]["count"] == 2
    manager.policy = manager.policy.model_copy(
        update={
            "network": manager.policy.network.model_copy(
                update={
                    "targets": [
                        target,
                        target.model_copy(update={"id": "extra", "hostname": "other.test"}),
                    ]
                }
            )
        }
    )
    assert len(manager.egress.network_for_run(scope.run_id)["targets"]) == 1


def test_frozen_resource_caps_cannot_expand_and_old_worker_close_is_owned_only(f):
    s = session(f)
    accepted = send(f, s)
    scope = claim(f, accepted)
    manager = f.services.bash.manager
    frozen = manager.policy.model_dump()
    frozen.update(cpu=0.5, max_cpu=1.0, memory_mib=256, max_memory_mib=512)
    with f.services.db.session(write=True) as db:
        db.get(AssistantRun, scope.run_id).context = {"sandboxPolicy": frozen, "policyVersion": "3"}
    policy = manager.policy_for(scope.run_id)
    assert (policy.cpu, policy.max_cpu, policy.memory_mib, policy.max_memory_mib) == (
        0.5,
        1.0,
        256,
        512,
    )
    row = instance(f, scope, "new-worker")
    with f.services.db.session(write=True) as db:
        stored = db.get(SandboxInstance, row.id)
        stored.state = "idle"
        stored.worker_owner = "replacement"
        stored.worker_fence = scope.fence + 1
    manager.close_instance = AsyncMock(return_value=True)
    asyncio.run(manager.reconcile(scope.owner, scope.fence, only_owned=True))
    manager.close_instance.assert_not_awaited()


def test_startup_reaps_expired_task_before_claiming_warmup_and_does_not_replay(f):
    s = session(f)
    scope = claim(f, send(f, s))
    manager = f.services.bash.manager
    row = instance(f, scope, "orphan")
    with f.services.db.session(write=True) as db:
        db.get(SandboxInstance, row.id).state = "running"
    f.clock.advance(3)

    async def cleanup(target, **_kwargs):
        with f.services.db.session(write=True) as db:
            db.get(SandboxInstance, target.id).state = "closed"
        return True

    manager.close_instance = AsyncMock(side_effect=cleanup)
    asyncio.run(manager.claim_warmup("startup-warmup", "new-warmup"))
    manager.close_instance.assert_awaited_once()
    with f.services.db.session() as db:
        assert db.get(AssistantRun, scope.run_id).status == "interrupted"
        assert db.get(SandboxInstance, "new-warmup").state == "warmup"


def test_capacity_wait_is_bounded_without_releasing_another_calls_lock(f):
    import pytest

    from app.core.errors import AppError

    manager = f.services.bash.manager
    manager.policy = manager.policy.model_copy(update={"queue_timeout_seconds": 1})

    async def exercise():
        await manager.lock.acquire()
        with pytest.raises(AppError, match="BUSY"):
            async with manager.serialize():
                raise AssertionError("Unowned slot entered")
        assert manager.lock.locked()
        manager.lock.release()
        async with manager.serialize():
            assert manager.lock.locked()
        assert not manager.lock.locked()

    asyncio.run(exercise())


def test_unconfirmed_process_cleanup_never_commits_workspace(f):
    from app.agent.bash import BashArguments
    from app.agent.tools import ToolRegistry
    from app.models.entities import AssistantToolCall

    registry = ToolRegistry(bash_enabled=True)
    f.services.assistant.registry = registry
    f.services.assistant.context.registry = registry
    s = session(f, mode="auto_sandbox")
    scope = claim(f, send(f, s, "bash: printf safe"))
    asyncio.run(f.services.assistant.model(scope))
    with f.services.db.session() as db:
        call = db.scalar(select(AssistantToolCall).where(AssistantToolCall.run_id == scope.run_id))
    manager = f.services.bash.manager
    row = instance(f, scope, "cleanup-unknown")
    manager.acquire = AsyncMock(return_value=row)
    manager.stats = AsyncMock(return_value={})
    manager.no_children = AsyncMock(return_value=False)
    manager.egress.collect = AsyncMock()
    manager.workspace = AsyncMock()
    manager.runner.cli = AsyncMock(return_value=(0, b"safe", b""))

    async def cleanup(target, **_kwargs):
        with f.services.db.session(write=True) as db:
            db.get(SandboxInstance, target.id).state = "unknown"
        return False

    manager.close_instance = AsyncMock(side_effect=cleanup)
    value, error = asyncio.run(
        manager.execute(scope, call.id, BashArguments(command="printf safe"))
    )
    assert error == "SANDBOX_ERROR" and not value.success
    manager.workspace.assert_not_awaited()
    assert scope.run_id in manager.busy_slots()
