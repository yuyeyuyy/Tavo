from app.agent.bash import (
    BashArguments,
    BashResult,
    NetworkRequestArguments,
    WorkspaceWriteArguments,
)
from app.agent.host import HostBashArguments
from app.agent.tools.definition import define_tool

SANDBOX_DESCRIPTION = (
    "Execute Bash/Python/tests in an isolated task workspace. Host access and direct network "
    "are forbidden. Use web_fetch for reading public web pages. Use Python tavo_http.request "
    "for sandbox API calls when allowed, and bound aliases for saved API credentials. "
    "Use tavo_pip for approved wheels. Use workspace_write chunks for oversized scripts."
)


def command_tools():
    return [
        define_tool(
            name,
            description,
            arguments,
            BashResult,
            read_only=False,
            execution="sandbox",
        )
        for name, description, arguments in (
            ("sandbox", SANDBOX_DESCRIPTION, BashArguments),
            (
                "workspace_write",
                "Write a UTF-8 chunk to a workspace-relative file. Use the returned SHA256 "
                "and offset for the next chunk.",
                WorkspaceWriteArguments,
            ),
            (
                "http_request",
                "Resume the exact sandbox HTTP operation that returned APPROVAL_REQUIRED, "
                "using its requestId. This does not rerun the parent script.",
                NetworkRequestArguments,
            ),
        )
    ] + [
        define_tool(
            "host_bash",
            "Execute Bash on the Python backend machine as its current OS user without "
            "container isolation. Use for system state, processes, logs and authorized "
            "administration. Prefer sandbox for scripts and web_fetch for public pages. "
            "Do not seek credentials, elevate privileges or replay unknown writes.",
            HostBashArguments,
            BashResult,
            read_only=False,
            execution="host",
        )
    ]
