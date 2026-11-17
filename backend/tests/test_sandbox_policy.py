import json

import pytest
from pydantic import ValidationError

from app.agent.bash import BashArguments, WorkspaceWriteArguments
from app.agent.bash_policy import classify
from app.agent.tools import ToolRegistry
from app.core.errors import AppError, Code
from app.core.sandbox_config import default_policy, load_policy
from app.llm.turns import proposal
from sandbox.runtime.tavo_sandbox.workspace import (
    WorkspaceFailure,
    baseline,
    manifest,
    operate,
    write_chunk,
)


def test_defaults_and_utf8_command_boundary_and_schema():
    policy = default_policy()
    assert (
        policy.command_max_bytes,
        policy.default_timeout_seconds,
        policy.max_timeout_seconds,
    ) == (32768, 60, 300)
    assert (
        policy.cpu,
        policy.max_cpu,
        policy.memory_mib,
        policy.max_memory_mib,
        policy.pids_limit,
        policy.max_sandboxes,
    ) == (1, 2, 512, 1024, 128, 1)
    assert BashArguments(command="x" * 32768).timeout_seconds == 60
    BashArguments(command="你" * 10922 + "xx", timeoutSeconds=300)
    registry = ToolRegistry(bash_enabled=True)
    for command in ("x" * 32769, "你" * 10923):
        with pytest.raises(AppError, match=Code.COMMAND_TOO_LONG):
            registry.validate("bash", {"command": command})
    with pytest.raises(ValidationError):
        BashArguments(command="pwd", timeoutSeconds=301)
    assert (
        proposal("call", "bash", json.dumps({"command": "x" * 16000})).arguments["command"]
        == "x" * 16000
    )


def test_config_is_strict_and_can_only_lower_or_select_resource_profiles(tmp_path):
    path = tmp_path / "sandbox.toml"
    path.write_text('memory_mib=1024\ncpu=2.0\n[network]\nprotected_cidrs=["203.0.113.0/24"]\n')
    value = load_policy(path)
    assert value.memory_mib == 1024 and "168.63.129.16/32" in value.network.protected_cidrs
    path.write_text("max_cpu=3.0\n")
    with pytest.raises(AppError, match=Code.SANDBOX_CONFIG_INVALID):
        load_policy(path)
    path.write_text('unknown_secret="not-a-real-key"\n')
    with pytest.raises(AppError) as error:
        load_policy(path)
    assert str(error.value) == Code.SANDBOX_CONFIG_INVALID


@pytest.mark.parametrize(
    "command", ["pwd", "ls -la", "cat note.txt", "git status", "git diff", "python --version"]
)
def test_low_local_commands_do_not_depend_on_model_review(command):
    registry = ToolRegistry(bash_enabled=True)
    risk = classify("bash", {"command": command}, "auto_sandbox", ["bash"], registry)
    assert risk.level == "LOW" and risk.action == "allow" and not risk.model_review


@pytest.mark.parametrize("mode", ["full_access", "auto_review", "request_approval"])
def test_command_permissions_do_not_grant_host_access_or_apply_to_file_tool(mode):
    registry = ToolRegistry(bash_enabled=True)
    allowed = list(registry.definitions)
    for command in ("cat /var/run/docker.sock", "cat /Users/owner/.ssh/id_rsa", "sudo id"):
        risk = classify("bash", {"command": command}, mode, allowed, registry)
        assert risk.action == "deny" and not risk.model_review
    write = classify(
        "workspace_write", {"path": "note.py", "content": "print(42)"}, mode, allowed, registry
    )
    assert write.action == "allow" and not write.model_review
    invalid = classify(
        "workspace_write", {"path": "../outside", "content": "x"}, mode, allowed, registry
    )
    assert invalid.action == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "python - <<'PY'\nprint('ok')\nPY",
        "pytest -q",
        "find . -exec python x.py \\;",
        "cat x | python",
        "printf hi > note.txt",
    ],
)
def test_general_code_remains_usable_but_not_claimed_statically_safe(command):
    registry = ToolRegistry(bash_enabled=True)
    risk = classify("bash", {"command": command}, "auto_sandbox", ["bash"], registry)
    assert risk.level == "MEDIUM" and risk.action == "allow"


def test_workspace_chunks_are_cas_bound_and_support_large_scripts(tmp_path):
    first = {
        "path": "src/example.py",
        "content": "#" * 16000,
        "offset": 0,
        "expectedSha256": None,
        "max_file_bytes": 67108864,
    }
    result = write_chunk(first, tmp_path)
    second = {
        **first,
        "offset": result["offset"],
        "expectedSha256": result["sha256"],
        "content": "x" * 16000,
    }
    result = write_chunk(second, tmp_path)
    assert result["offset"] == 32000 and (tmp_path / "src/example.py").stat().st_size == 32000
    with pytest.raises(WorkspaceFailure, match="REVISION_CONFLICT"):
        write_chunk(second, tmp_path)
    replace = {**first, "expectedSha256": result["sha256"], "content": "print(42)\n"}
    write_chunk(replace, tmp_path)
    assert (tmp_path / "src/example.py").read_text() == "print(42)\n"
    with pytest.raises(ValidationError):
        WorkspaceWriteArguments(path="../host", content="x")


def test_workspace_snapshots_do_not_overwrite_original_before_commit_pointer(tmp_path):
    source, work = tmp_path / "source", tmp_path / "work"
    source.mkdir()
    work.mkdir()
    (source / "original.py").write_text("old")
    limits = default_policy().model_dump()
    operate({"action": "initialize", "limits": limits}, source, work)
    (work / "original.py").unlink()
    (work / "fixed.py").write_text("print(42)")
    result = operate({"action": "inspect", "limits": limits}, source, work)
    assert result["deleted"] == 1
    assert (source / "original.py").read_text() == "old"
    revision = "a" * 32
    operate(
        {
            "action": "commit",
            "limits": limits,
            "expected_digest": result["digest"],
            "new_revision": revision,
        },
        source,
        work,
    )
    assert (source / "original.py").read_text() == "old"
    assert (baseline(source, revision) / "fixed.py").read_text() == "print(42)"


def test_workspace_rejects_symlinks_hardlinks_and_special_files(tmp_path):
    (tmp_path / "unsafe").symlink_to("/etc/passwd")
    with pytest.raises(WorkspaceFailure, match="POLICY_DENIED"):
        manifest(tmp_path, default_policy().model_dump())
    with pytest.raises((WorkspaceFailure, OSError)):
        write_chunk(
            {
                "path": "unsafe",
                "content": "x",
                "offset": 0,
                "expectedSha256": None,
                "max_file_bytes": 1000,
            },
            tmp_path,
        )
