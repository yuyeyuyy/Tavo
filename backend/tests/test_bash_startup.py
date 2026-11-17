import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.agent import bash as module
from app.core import startup
from app.core.errors import AppError
from app.main import create_app
from app.services.application import Services

INFO = {
    "MemoryLimit": True,
    "SwapLimit": True,
    "PidsLimit": True,
    "CpuCfsQuota": True,
    "SecurityOptions": ["name=seccomp,profile=builtin"],
}
IMAGE = b"sha256:" + b"a" * 64 + b" linux 3 " + module.image_fingerprint().encode()


def configured(f, **changes):
    runner = f.services.bash
    policy = f.settings.sandbox.model_copy(
        update={"network": f.settings.sandbox.network.model_copy(update={"mode": "none"})}
    )
    runner.settings = replace(f.settings, bash_enabled=True, sandbox=policy, **changes)
    runner.manager.policy = policy  # This suite fakes Docker CLI, not the network gateway.
    runner.executable = "/synthetic/docker"
    return runner


def engine(runner, *, missing=False, build_rc=0, health_rc=0, info_rc=0):
    calls = []
    built = False

    async def cli(*args, **kwargs):
        nonlocal built
        calls.append((args, kwargs))
        if args[0] == "info":
            return info_rc, json.dumps(INFO).encode(), b"private-docker-error"
        if args[:2] == ("image", "inspect"):
            return (1, b"", b"") if missing and not built else (0, IMAGE, b"")
        if args[0] == "build":
            built = True
            return build_rc, b"", b"private-build-log"
        if args[0] == "run":
            return health_rc, b"tavo-bash-ready", b""
        raise AssertionError("Unexpected Docker request")

    runner.cli = cli
    runner.remove = AsyncMock(return_value=True)
    return calls


def test_prepare_reuses_image_and_warms_real_shell_configuration(f, capsys):
    runner = configured(f)
    calls = engine(runner)
    asyncio.run(runner.prepare())
    argv = next(args for args, _ in calls if args[0] == "run")
    for required in (
        "--rm",
        "--network=none",
        "--read-only",
        "--user=10001:10001",
        "--memory=512m",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ):
        assert required in argv
    assert "--mount" not in argv and "--privileged" not in argv
    assert not any(args[0] == "build" for args, _ in calls)
    assert runner.available
    runner.remove.assert_awaited_once()
    text = capsys.readouterr().err
    assert "本地执行镜像已存在，直接复用，无需拉取或构建" in text
    assert "Bash 沙箱准备完成，预热容器已清理" in text
    assert [text.index(f"[{step}/4]") for step in range(1, 5)] == sorted(
        text.index(f"[{step}/4]") for step in range(1, 5)
    )
    assert "private-docker-error" not in text


def test_prepare_builds_only_missing_trusted_default_image(f, capsys):
    runner = configured(f)
    calls = engine(runner, missing=True)
    asyncio.run(runner.prepare())
    builds = [(args, kwargs) for args, kwargs in calls if args[0] == "build"]
    assert len(builds) == 1 and builds[0][0][2] == module.DEFAULT_BASH_IMAGE
    assert builds[0][1]["timeout"] == runner.settings.sandbox.build_timeout_seconds
    assert runner.available
    text = capsys.readouterr().err
    assert "默认镜像缺失或版本过期，正在构建" in text and "构建完成并验证通过" in text
    assert "private-build-log" not in text


@pytest.mark.parametrize(
    "case,code",
    [
        ("daemon", "DOCKER_NOT_RUNNING"),
        ("custom", "BASH_IMAGE_MISSING"),
        ("build", "BASH_IMAGE_BUILD_FAILED"),
        ("warmup", "BASH_STARTUP_FAILED"),
        ("cleanup", "BASH_STARTUP_FAILED"),
        ("missing_cli", "DOCKER_NOT_INSTALLED"),
    ],
)
def test_prepare_refuses_failure_without_becoming_ready(f, case, code, capsys):
    runner = configured(f, **({"bash_image": "operator/custom:local"} if case == "custom" else {}))
    calls = engine(
        runner,
        missing=case in ("custom", "build"),
        build_rc=1 if case == "build" else 0,
        health_rc=1 if case == "warmup" else 0,
        info_rc=1 if case == "daemon" else 0,
    )
    if case == "cleanup":
        runner.remove = AsyncMock(return_value=False)
    if case == "missing_cli":
        runner.executable = None
    with pytest.raises(AppError, match=code):
        asyncio.run(runner.prepare())
    if case in ("daemon", "custom", "missing_cli"):
        assert not any(args[0] in ("build", "run") for args, _ in calls)
    assert "Bash 沙箱准备完成" not in capsys.readouterr().err


@pytest.mark.parametrize("failed", [False, True])
def test_slow_build_reports_waiting_and_stops_progress_after_return(f, monkeypatch, capsys, failed):
    monkeypatch.setattr(startup, "PROGRESS_INTERVAL", 0.005)
    runner = configured(f)
    engine(runner, missing=True, build_rc=int(failed))
    original = runner.cli

    async def slow(*args, **kwargs):
        if args[0] == "build":
            await asyncio.sleep(0.03)
        return await original(*args, **kwargs)

    runner.cli = slow
    capsys.readouterr()

    async def exercise():
        if failed:
            with pytest.raises(AppError, match="BASH_IMAGE_BUILD_FAILED"):
                await runner.prepare()
        else:
            await runner.prepare()
        text = capsys.readouterr().err
        assert "[2/4] 默认镜像缺失" in text and "已等待" in text
        assert "private-build-log" not in text
        assert ("Bash 沙箱准备完成" in text) is not failed
        await asyncio.sleep(0.02)
        assert capsys.readouterr().err == ""

    asyncio.run(exercise())


def test_prepare_build_timeout_and_disabled_mode(f):
    runner = configured(f)
    calls = engine(runner, missing=True)
    original = runner.cli

    async def timeout(*args, **kwargs):
        if args[0] == "build":
            raise TimeoutError
        return await original(*args, **kwargs)

    runner.cli = timeout
    with pytest.raises(AppError, match="BASH_IMAGE_BUILD_FAILED"):
        asyncio.run(runner.prepare())
    runner.settings = f.settings
    calls.clear()
    asyncio.run(runner.prepare())
    assert calls == []


def test_asgi_startup_failure_closes_services_and_never_starts_worker(f, caplog):
    made = []

    def factory(settings):
        services = Services(settings)
        services.bash.prepare = AsyncMock(side_effect=AppError(module.Code.DOCKER_NOT_RUNNING))
        original = services.close
        services.close = AsyncMock(wraps=original)
        made.append(services)
        return services

    app = create_app(f.settings, factory)
    with pytest.raises(AppError, match="DOCKER_NOT_RUNNING"), TestClient(app):
        pass
    made[0].close.assert_awaited_once()
    assert not hasattr(app.state, "worker")
    assert "Docker 服务未启动或无法连接" in caplog.text


def test_cancelled_preparation_awaits_warmup_cleanup(f):
    runner = configured(f)
    engine(runner)
    original = runner.cli

    async def exercise():
        entered = asyncio.Event()

        async def blocked(*args, **kwargs):
            if args[0] == "run":
                entered.set()
                await asyncio.Event().wait()
            return await original(*args, **kwargs)

        runner.cli = blocked
        task = asyncio.create_task(runner.prepare())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        runner.remove.assert_awaited_once()

    asyncio.run(exercise())


def test_cancellation_during_cleanup_is_not_swallowed(f):
    runner = configured(f)
    engine(runner)

    async def exercise():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def cleanup(*_args):
            entered.set()
            await finish.wait()
            return True

        runner.remove = AsyncMock(side_effect=cleanup)
        task = asyncio.create_task(runner.prepare())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        runner.remove.assert_awaited_once()

    asyncio.run(exercise())
