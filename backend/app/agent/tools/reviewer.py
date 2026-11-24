from app.core.errors import AppError


class ToolCallReviewer:
    version = "2"

    def __init__(self, registry):
        self.registry = registry

    def review(self, name, arguments, allowed, mode):
        # 工具不在白名单
        if name not in allowed or mode == "disabled":
            return "deny", "TOOL_NOT_ALLOWED"
        try:
            # 验证参数是否合法
            self.registry.validate(name, arguments)
        except AppError as error:
            return "deny", error.code.value
        tool = self.registry.definitions[name]
        if mode == "ask" or (not tool.spec.read_only and mode != "auto_sandbox"):
            return "ask", "USER_CONFIRMATION_REQUIRED"
        return "allow", "READ_ONLY_TOOL" if tool.spec.read_only else "SANDBOX_CAPABILITY"
