from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Annotated, Literal, Protocol

from pydantic import Field, ValidationError

from app.agent.bash import (
    BashArguments,
    BashResult,
    NetworkRequestArguments,
    WorkspaceWriteArguments,
)
from app.agent.contracts import ToolSpec
from app.agent.host import HostBashArguments
from app.api.base import DTO
from app.core.errors import AppError, Code
from app.core.sandbox_config import default_policy


class EmptyArguments(DTO):
    pass


class ClockResult(DTO):
    utc: str


Number = Annotated[str, Field(pattern=r"^-?[0-9]{1,18}(\.[0-9]{1,12})?$")]


class Calculation(DTO):
    operation: Literal["add", "subtract", "multiply", "divide"]
    left: Number
    right: Number


class CalculationResult(DTO):
    result: Annotated[str, Field(max_length=100)]


@dataclass(frozen=True)
class ToolContext:
    now: Callable[[], str]


class Tool(Protocol):
    spec: ToolSpec
    arguments: type[DTO]
    result: type[DTO]

    def execute(self, arguments: DTO, context: ToolContext) -> DTO: ...


@dataclass(frozen=True)
class ToolDefinition:
    spec: ToolSpec
    arguments: type[DTO]
    result: type[DTO]
    execute: Callable | None


def calculate(value, _context):
    with localcontext() as context:
        context.prec = 64
        left, right = Decimal(value.left), Decimal(value.right)
        if value.operation == "divide" and right == 0:
            raise AppError(Code.INVALID_INPUT)
        result = {
            "add": lambda: left + right,
            "subtract": lambda: left - right,
            "multiply": lambda: left * right,
            "divide": lambda: left / right,
        }[value.operation]()
        if not result.is_finite():
            raise AppError(Code.INVALID_DATA)
        return CalculationResult(result=format(result, "f"))


class ToolRegistry:
    def __init__(self, definitions=None, *, bash_enabled=False, host_enabled=False, policy=None):
        self.policy = policy or default_policy()
        if definitions is None:
            definitions = [
                ToolDefinition(
                    ToolSpec(
                        name="get_current_time",
                        description="Read the current UTC time. No arguments or external access.",
                        input_schema=EmptyArguments.model_json_schema(),
                        output_schema=ClockResult.model_json_schema(),
                    ),
                    EmptyArguments,
                    ClockResult,
                    lambda _args, context: ClockResult(utc=context.now()),
                ),
                ToolDefinition(
                    ToolSpec(
                        name="calculate",
                        description="Calculate add/subtract/multiply/divide on two decimal strings. No expressions or code are executed.",
                        input_schema=Calculation.model_json_schema(),
                        output_schema=CalculationResult.model_json_schema(),
                    ),
                    Calculation,
                    CalculationResult,
                    calculate,
                ),
            ]
        if bash_enabled:
            definitions = [
                *definitions,
                ToolDefinition(
                    ToolSpec(
                        name="bash",
                        description="Execute Bash/Python/tests in the isolated task workspace. Host access and direct network are forbidden. Use Python tavo_http.request('https://host/path', method='POST', json={...}) for public HTTPS when permitted, or a configured alias for bound API credentials. Use tavo_pip only for approved wheels. Files persist; oversized scripts must use workspace_write chunks. Read structured errors.",
                        read_only=False,
                        input_schema=BashArguments.model_json_schema(),
                        output_schema=BashResult.model_json_schema(),
                    ),
                    BashArguments,
                    BashResult,
                    None,
                ),
            ]
            for name, description, arguments in (
                (
                    "workspace_write",
                    "Write a UTF-8 chunk to a workspace-relative file. Use returned SHA256 and next offset for subsequent chunks. Never truncate a large script to fit Bash.",
                    WorkspaceWriteArguments,
                ),
                (
                    "http_request",
                    "Resume one exact HTTP operation that returned APPROVAL_REQUIRED from tavo_http, by its requestId. Approval never reruns the parent script.",
                    NetworkRequestArguments,
                ),
            ):
                definitions.append(
                    ToolDefinition(
                        ToolSpec(
                            name=name,
                            description=description,
                            read_only=False,
                            input_schema=arguments.model_json_schema(),
                            output_schema=BashResult.model_json_schema(),
                        ),
                        arguments,
                        BashResult,
                        None,
                    )
                )
        if bash_enabled:
            legacy = next(d for d in definitions if d.spec.name == "bash")
            definitions.append(
                ToolDefinition(
                    legacy.spec.model_copy(update={"name": "sandbox"}, deep=True),
                    BashArguments,
                    BashResult,
                    None,
                )
            )
        if host_enabled:
            definitions.append(
                ToolDefinition(
                    ToolSpec(
                        name="host_bash",
                        description="Execute real Bash on the Python backend machine as its current OS user, without container isolation. Use for system/process/log inspection, Docker management and authorized project deployment. cwd is a real directory. Prefer sandbox for scripts, data processing and web fetching. Do not read credentials or elevate privileges; environment and outputs are guarded. Unknown writes must not be replayed.",
                        read_only=False,
                        input_schema=HostBashArguments.model_json_schema(),
                        output_schema=BashResult.model_json_schema(),
                    ),
                    HostBashArguments,
                    BashResult,
                    None,
                )
            )
        self.definitions = {}
        for definition in definitions:
            if definition.spec.name in self.definitions:
                raise AppError(Code.INVALID_INPUT)
            # Strict Responses schemas require every declared property, including an empty list.
            schema = definition.spec.input_schema
            if definition.spec.name in {"bash", "sandbox", "host_bash"}:
                limits = self.policy.host if definition.spec.name == "host_bash" else self.policy
                schema["properties"]["command"]["maxLength"] = limits.command_max_bytes
                schema["properties"]["timeoutSeconds"]["maximum"] = limits.max_timeout_seconds
            for item in schema.get("properties", {}).values():
                item.pop("default", None)
            schema.setdefault("required", list(schema.get("properties", {})))
            self.definitions[definition.spec.name] = definition

    def specs(self, execution_scope="sandbox_only"):
        excluded = {"bash"} if execution_scope == "host_and_sandbox" else {"sandbox", "host_bash"}
        return [tool.spec for tool in self.definitions.values() if tool.spec.name not in excluded]

    def validate(self, name, arguments):
        tool = self.definitions.get(name)
        if tool is None or arguments is None:
            raise AppError(Code.FORBIDDEN)
        try:
            return tool, tool.arguments.model_validate(arguments, context={"policy": self.policy})
        except ValidationError as error:
            if name in {"bash", "sandbox", "host_bash"} and any(
                (e["loc"] == ("command",) and e["type"] == "string_too_long")
                or e["type"] == "command_too_long"
                for e in error.errors(include_input=False)
            ):
                raise AppError(Code.COMMAND_TOO_LONG) from None
            if any(e["type"] == "resource_limit" for e in error.errors(include_input=False)):
                raise AppError(Code.RESOURCE_LIMIT) from None
            raise AppError(Code.INVALID_INPUT) from None


class ToolCallReviewer:
    version = "2"

    def __init__(self, registry):
        self.registry = registry

    def review(self, name, arguments, allowed, mode):
        if name not in allowed or mode == "disabled":
            return "deny", "TOOL_NOT_ALLOWED"
        try:
            self.registry.validate(name, arguments)
        except AppError as error:
            return "deny", error.code.value
        tool = self.registry.definitions[name]
        if mode == "ask" or (not tool.spec.read_only and mode != "auto_sandbox"):
            return "ask", "USER_CONFIRMATION_REQUIRED"
        return "allow", "READ_ONLY_TOOL" if tool.spec.read_only else "SANDBOX_CAPABILITY"
