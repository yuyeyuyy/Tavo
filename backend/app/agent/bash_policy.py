"""Classification is advice; container, snapshot commit and egress are the actual boundaries."""

import shlex
from dataclasses import dataclass

from app.agent.contracts import COMMAND_PERMISSION_MODES
from app.core.errors import Code


@dataclass(frozen=True)
class Risk:
    level: str
    action: str
    reason: str
    model_review: bool = False


def classify(name, arguments, mode, allowed, registry):
    if name not in allowed or mode == "disabled":
        return Risk("HIGH", "deny", Code.POLICY_DENIED)
    try:
        _, args = registry.validate(name, arguments)
    except Exception as error:
        from app.core.errors import AppError

        if not isinstance(error, AppError):
            raise
        return Risk("HIGH", "deny", str(error.code))
    if name == "http_request":
        if mode in COMMAND_PERMISSION_MODES:
            return Risk("MEDIUM", "allow", "TOOL_CAPABILITY")
        return Risk("HIGH", "ask", Code.APPROVAL_REQUIRED)
    if mode in COMMAND_PERMISSION_MODES and name not in {"sandbox", "host_bash"}:
        return Risk(
            "LOW" if registry.definitions[name].spec.read_only else "MEDIUM",
            "allow",
            "TOOL_CAPABILITY",
        )
    if name not in {"sandbox", "host_bash", "workspace_write"}:
        return Risk("LOW", "allow" if mode != "ask" else "ask", "READ_ONLY_TOOL")
    if name == "workspace_write":
        level = "MEDIUM"
    else:
        command = args.command
        # Only a small grammar is classified LOW. No evaluation, shell expansion or guessing
        # Python semantics. All other programs remain usable inside the MEDIUM capability.
        try:
            words = shlex.split(command)
        except ValueError:
            words = []
        simple = words and not any(c in command for c in "$`;&|<>(){}\n\\")
        base = words[0] if words else ""
        low = simple and (
            (
                base in {"pwd", "ls", "cat", "head", "tail", "wc", "grep", "id", "uname"}
                and not any(w.startswith("--exec") for w in words)
            )
            or (base == "git" and len(words) >= 2 and words[1] in {"status", "diff"})
            or (base in {"python", "python3"} and words[1:] == ["--version"])
        )
        level = "LOW" if low else "MEDIUM"
        # A declared attempt at a host/system capability is never delegated to a model.
        # Hidden/encoded actions still cannot pass the actual filesystem/network boundaries.
        host_paths = (
            "/var/run/docker.sock",
            "/run/docker.sock",
            "/Users/",
            "/host/",
            "/root/.ssh",
            "/root/.aws",
        )
        if name != "host_bash" and (
            any(p in command for p in host_paths)
            or (simple and base in {"sudo", "docker", "mount", "nsenter"})
        ):
            return Risk("HIGH", "deny", Code.POLICY_DENIED)
        if mode in COMMAND_PERMISSION_MODES:
            if mode == "request_approval":
                return Risk(level, "ask", Code.APPROVAL_REQUIRED)
            if mode == "auto_review":
                return Risk(level, "allow", "MODEL_REVIEW_REQUIRED", model_review=True)
            return Risk(
                level, "allow", "FULL_HOST_ACCESS" if name == "host_bash" else "FULL_SANDBOX_ACCESS"
            )
        if (
            base == "rm"
            or (base == "git" and len(words) > 1 and words[1] in {"clean", "reset"})
            or "eval " in command
            or "base64.b64decode" in command
        ):
            return Risk("HIGH", "ask", Code.APPROVAL_REQUIRED, model_review=True)
    if mode in {"ask", "auto_readonly"}:
        return Risk(level, "ask", Code.APPROVAL_REQUIRED)
    return Risk(level, "allow", "LOCAL_WORKSPACE_CAPABILITY")
