import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def environment(directory):
    value = {**os.environ, "TAVO_BASH_ENABLED": "1", "TAVO_DATA_DIR": str(directory)}
    for key in ("DOCKER_CONTEXT", "DOCKER_HOST"):
        value.pop(key, None)
    return value


@pytest.mark.parametrize("command", ["serve", "worker"])
def test_cli_unavailable_docker_exits_with_clear_message_without_starting_worker(tmp_path, command):
    data = tmp_path / "data"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\necho private-startup-transport-details >&2\nexit 1\n")
    docker.chmod(0o700)
    env = environment(data)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    init = subprocess.run(
        [sys.executable, "-m", "app.cli", "init"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert init.returncode == 0  # initialization intentionally has no Docker dependency
    args = [sys.executable, "-m", "app.cli", command]
    if command == "serve":
        args += ["--port", "0"]
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, timeout=15)
    text = result.stderr.decode()
    assert result.returncode != 0
    assert "Docker 服务未启动或无法连接" in text and "后端已停止" in text
    assert "private-startup-transport-details" not in text
    assert "[1/4] 正在检查 Docker" in text
    assert "Bash 沙箱准备完成" not in text and "启动完成" not in text
    with sqlite3.connect(data / "tavo.sqlite") as sql:
        assert sql.execute("SELECT COUNT(*) FROM worker_lease").fetchone() == (0,)
