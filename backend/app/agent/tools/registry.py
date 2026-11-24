import math
import re
from dataclasses import replace
from types import MappingProxyType

from pydantic import ValidationError

from app.agent.contracts import MAX_TOOL_DEFINITIONS
from app.agent.tools.builtin.commands import command_tools
from app.agent.tools.builtin.local import local_tools
from app.agent.tools.definition import ToolDefinition
from app.core.errors import AppError, Code
from app.core.sandbox_config import default_policy


class ToolRegistry:
    def __init__(
        self,
        definitions=None,
        *,
        builtins=(),
        disabled=(),
        policy=None,
        bash_enabled=None,
        host_enabled=None,
    ):
        self.policy = policy or default_policy()
        self._definitions = {}
        self.definitions = MappingProxyType(self._definitions)
        disabled = set(disabled)
        # Compatibility for other callers of the former registry constructor.
        if bash_enabled is False:
            disabled.update({"sandbox", "workspace_write", "http_request"})
        if host_enabled is False:
            disabled.add("host_bash")
        self.disabled = frozenset(disabled)
        defaults = (
            [*local_tools(), *command_tools(), *builtins] if definitions is None else definitions
        )
        for definition in defaults:
            self._register(definition)

    def _register(self, definition: ToolDefinition):
        name = definition.spec.name
        if (
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)
            or name == "bash"
            or name in self.definitions
            or len(self.definitions) >= MAX_TOOL_DEFINITIONS
            or definition.category not in {"builtin", "extension"}
            or definition.execution not in {"sync", "async", "sandbox", "host"}
            or (definition.execution in {"sync", "async"} and not callable(definition.execute))
            or not math.isfinite(definition.timeout_seconds)
            or definition.timeout_seconds <= 0
        ):
            raise AppError(Code.INVALID_INPUT)
        spec = definition.spec.model_copy(deep=True)
        schema = spec.input_schema
        properties = schema.get("properties", {})
        if name in {"sandbox", "host_bash"}:
            limits = self.policy.host if name == "host_bash" else self.policy
            properties["command"]["maxLength"] = limits.command_max_bytes
            properties["timeoutSeconds"]["maximum"] = limits.max_timeout_seconds
        for item in properties.values():
            item.pop("default", None)
        # Responses strict mode requires every property to be included in the request.
        schema["required"] = list(properties)
        self._definitions[name] = replace(definition, spec=spec)

    def register_extension(self, definition: ToolDefinition):
        """Register a trusted in-process read-only callable supplied by application code."""
        if definition.execution not in {"sync", "async"} or not definition.spec.read_only:
            raise AppError(Code.INVALID_INPUT)
        self._register(replace(definition, category="extension"))

    def specs(self, execution_scope="sandbox_only", *, category=None):
        return [
            tool.spec.model_copy(deep=True)
            for name, tool in self.definitions.items()
            if (name != "host_bash" or execution_scope == "host_and_sandbox")
            and name not in self.disabled
            and (category is None or tool.category == category)
        ]

    # 验证参数是否合法
    def validate(self, name, arguments):
        tool = self.definitions.get(name)
        if tool is None or name in self.disabled or arguments is None:
            raise AppError(Code.FORBIDDEN)
        try:
            return tool, tool.arguments.model_validate(arguments, context={"policy": self.policy})
        except ValidationError as error:
            issues = error.errors(include_input=False)
            if name in {"sandbox", "host_bash"} and any(
                (e["loc"] == ("command",) and e["type"] == "string_too_long")
                or e["type"] == "command_too_long"
                for e in issues
            ):
                raise AppError(Code.COMMAND_TOO_LONG) from None
            if any(e["type"] == "resource_limit" for e in issues):
                raise AppError(Code.RESOURCE_LIMIT) from None
            raise AppError(Code.INVALID_INPUT) from None
