import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent import bash as module
from app.agent.bash import BashRunner

pytestmark = pytest.mark.skipif(
    os.environ.get("TAVO_TEST_DOCKER") != "1", reason="Explicit real Docker startup tests"
)


def test_real_startup_warms_and_removes_container_before_ready(f, capsys):
    runner = BashRunner(replace(f.settings, bash_enabled=True), f.services.db, f.clock.now)
    calls = []
    original = runner.cli

    async def observed(*args, **kwargs):
        calls.append(args)
        return await original(*args, **kwargs)

    runner.cli = observed

    async def exercise():
        await runner.prepare()
        assert runner.available
        run = next(args for args in calls if args[0] == "run")
        assert "--rm" in run and "--mount" not in run
        rc, out, _ = await runner.cli(
            "ps",
            "--all",
            "--filter",
            "label=tavo.instance=" + runner.instance,
            "--filter",
            "label=tavo.kind=warmup",
            "--format",
            "{{.ID}}",
        )
        assert rc == 0 and not out.strip()

    asyncio.run(exercise())
    assert "Bash 沙箱准备完成，预热容器已清理" in capsys.readouterr().err


def test_real_missing_default_image_build_uses_isolated_test_tag(f, monkeypatch, capsys):
    tag = "tavo-startup-test:" + uuid4().hex
    monkeypatch.setattr(module, "DEFAULT_BASH_IMAGE", tag)
    runner = BashRunner(
        replace(f.settings, bash_enabled=True, bash_image=tag), f.services.db, f.clock.now
    )

    async def exercise():
        try:
            await runner.prepare()
            assert runner.available and runner.image_id
        finally:
            rc, _, _ = await runner.cli("image", "rm", tag)
            assert rc == 0  # only untag this test's image; never remove the user's default image

    asyncio.run(exercise())
    text = capsys.readouterr().err
    assert "默认镜像缺失或版本过期，正在构建" in text and "构建完成并验证通过" in text


@pytest.mark.parametrize("command", ["serve", "worker"])
def test_real_cli_success_displays_progress_and_readiness(f, tmp_path, command):
    env = {**os.environ, "TAVO_BASH_ENABLED": "1", "TAVO_DATA_DIR": str(f.settings.data_dir)}
    args = [sys.executable, "-m", "app.cli", command]
    ready = (
        "独立 Worker 启动完成" if command == "worker" else "Uvicorn running on http://127.0.0.1:"
    )
    if command == "serve":
        args += ["--port", "0"]
    log = tmp_path / "startup.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            args,
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                text = log.read_text()
                if ready in text or process.poll() is not None:
                    break
                time.sleep(0.05)
            assert ready in text
            assert "本地执行镜像已存在，直接复用" in text
            assert text.index("[1/4]") < text.index("[4/4]") < text.index(ready)
            assert "Bash 沙箱准备完成，预热容器已清理" in text
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_real_docker_client_unreachable_socket_stops_backend(f):
    assert shutil.which("docker")
    env = {
        **os.environ,
        "TAVO_BASH_ENABLED": "1",
        "TAVO_DATA_DIR": str(f.settings.data_dir),
        "DOCKER_HOST": "unix://" + str(f.settings.data_dir / "absent-docker.sock"),
    }
    env.pop("DOCKER_CONTEXT", None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "serve", "--port", str(port)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode != 0
    assert "Docker 服务未启动或无法连接" in result.stderr.decode()
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", port)) != 0
