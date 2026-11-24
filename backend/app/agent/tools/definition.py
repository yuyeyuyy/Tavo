from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.agent.contracts import ToolSpec
from app.api.base import DTO

ToolCategory = Literal["builtin", "extension"]
ExecutionKind = Literal["sync", "async", "sandbox", "host"]


@dataclass(frozen=True)
class ToolContext:
    now: Callable[[], str]
    web_policy: dict | None = None
    cancelled: Callable[[], bool] | None = None


@dataclass(frozen=True)
class ToolDefinition:
    spec: ToolSpec
    arguments: type[DTO]
    result: type[DTO]
    execute: Callable | None
    execution: ExecutionKind = "sync"
    category: ToolCategory = "builtin"
    timeout_seconds: float = 2


def define_tool(
    name,
    description,
    arguments,
    result,
    execute=None,
    *,
    read_only=True,
    execution="sync",
    timeout_seconds=2,
):
    return ToolDefinition(
        ToolSpec(
            name=name,
            description=description,
            read_only=read_only,
            input_schema=arguments.model_json_schema(),
            output_schema=result.model_json_schema(),
        ),
        arguments,
        result,
        execute,
        execution=execution,
        timeout_seconds=timeout_seconds,
    )
