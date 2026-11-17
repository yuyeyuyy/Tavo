from enum import StrEnum


class Code(StrEnum):
    POLICY_DENIED = "POLICY_DENIED"
    MODEL_REVIEW_DENIED = "MODEL_REVIEW_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    COMMAND_TOO_LONG = "COMMAND_TOO_LONG"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    NETWORK_DENIED = "NETWORK_DENIED"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    HOST_UNAVAILABLE = "HOST_UNAVAILABLE"
    SANDBOX_CONFIG_INVALID = "SANDBOX_CONFIG_INVALID"
    DOCKER_NOT_INSTALLED = "DOCKER_NOT_INSTALLED"
    DOCKER_NOT_RUNNING = "DOCKER_NOT_RUNNING"
    BASH_ISOLATION_UNSUPPORTED = "BASH_ISOLATION_UNSUPPORTED"
    BASH_IMAGE_MISSING = "BASH_IMAGE_MISSING"
    BASH_IMAGE_BUILD_FAILED = "BASH_IMAGE_BUILD_FAILED"
    BASH_STARTUP_FAILED = "BASH_STARTUP_FAILED"

    BASH_UNAVAILABLE = "BASH_UNAVAILABLE"
    TOOL_EXECUTION_UNKNOWN = "TOOL_EXECUTION_UNKNOWN"
    TOOL_OUTPUT_LIMIT = "TOOL_OUTPUT_LIMIT"
    TOOL_COMMAND_FAILED = "TOOL_COMMAND_FAILED"
    TOOL_REVIEW_FAILED = "TOOL_REVIEW_FAILED"
    TOOL_LOOP_DETECTED = "TOOL_LOOP_DETECTED"
    FEATURE_REMOVED = "FEATURE_REMOVED"
    REVIEW_EXPIRED = "REVIEW_EXPIRED"
    INVALID_INPUT = "INVALID_INPUT"
    FORBIDDEN = "FORBIDDEN"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    NOT_FOUND = "NOT_FOUND"
    BUSY = "BUSY"
    DUPLICATE_CONNECTION = "DUPLICATE_CONNECTION"
    INVALID_STATE = "INVALID_STATE"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
    STORAGE_FAILED = "STORAGE_FAILED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NETWORK = "NETWORK"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    INVALID_DATA = "INVALID_DATA"
    INTERRUPTED = "INTERRUPTED"
    INTERNAL = "INTERNAL"
    LEASE_LOST = "LEASE_LOST"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
    TOOL_BUDGET_EXCEEDED = "TOOL_BUDGET_EXCEEDED"
    TOOL_FAILED = "TOOL_FAILED"
    MODEL_CONFIG_INVALID = "MODEL_CONFIG_INVALID"
    MODEL_NOT_CONFIGURED = "MODEL_NOT_CONFIGURED"
    MODEL_AUTH_FAILED = "MODEL_AUTH_FAILED"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_REQUEST_INVALID = "MODEL_REQUEST_INVALID"
    MODEL_OUTPUT_LIMIT = "MODEL_OUTPUT_LIMIT"
    MODEL_EMPTY_RESPONSE = "MODEL_EMPTY_RESPONSE"
    MODEL_REJECTED = "MODEL_REJECTED"
    MODEL_RESPONSE_INVALID = "MODEL_RESPONSE_INVALID"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_PROFILE_LIMIT = "MODEL_PROFILE_LIMIT"
    SECRET_CHALLENGE_INVALID = "SECRET_CHALLENGE_INVALID"
    SECRET_SUBMISSION_INVALID = "SECRET_SUBMISSION_INVALID"
    SECRET_REVISION_CONFLICT = "SECRET_REVISION_CONFLICT"
    SECRET_ENCRYPTION_UNAVAILABLE = "SECRET_ENCRYPTION_UNAVAILABLE"


MESSAGES = {
    Code.POLICY_DENIED: "操作超出已授权的沙箱能力，后端规则拒绝执行。",
    Code.MODEL_REVIEW_DENIED: "模型风险复核拒绝执行，请查看审核摘要或调整操作。",
    Code.APPROVAL_REQUIRED: "该具体操作需要用户明确批准，尚未执行或提交。",
    Code.COMMAND_TOO_LONG: "命令超过配置的 UTF-8 字节上限，请分块写入脚本文件后执行，不要截断。",
    Code.RESOURCE_LIMIT: "执行触及内存、进程、文件或工作区资源限制。",
    Code.NETWORK_DENIED: "网络目标或操作未获授权，或属于受保护地址。",
    Code.SANDBOX_ERROR: "沙箱启动、状态核验或清理失败，未确认的操作不会自动重跑。",
    Code.EXECUTION_ERROR: "命令执行失败，请根据真实退出码和输出修复后重试。",
    Code.HOST_UNAVAILABLE: "宿主机 Bash 已关闭，或当前平台不支持。",
    Code.SANDBOX_CONFIG_INVALID: "沙箱配置不合法，后端已停止；请检查资源与网络策略。",
    Code.DOCKER_NOT_INSTALLED: "未找到 Docker 命令，请安装 Docker 后重新启动；后端已停止。",
    Code.DOCKER_NOT_RUNNING: "Docker 服务未启动或无法连接，请启动 Docker Desktop / Docker Engine 后重试；后端已停止。",
    Code.BASH_ISOLATION_UNSUPPORTED: "Docker 不支持所需的沙箱隔离或资源限制；后端已停止。",
    Code.BASH_IMAGE_MISSING: "找不到配置的 Bash 执行镜像，请准备该镜像后重试；后端已停止。",
    Code.BASH_IMAGE_BUILD_FAILED: "默认 Bash 执行镜像构建失败或超时，请检查 Docker、网络和镜像构建文件；后端已停止。",
    Code.BASH_STARTUP_FAILED: "Bash 沙箱预热或清理失败，请检查 Docker 和执行镜像；后端已停止。",
    Code.BASH_UNAVAILABLE: "沙箱不可用，请检查服务端 Docker 和执行镜像。",
    Code.TOOL_EXECUTION_UNKNOWN: "命令可能已经修改工作区，执行结果未知；再次执行需要确认。",
    Code.TOOL_OUTPUT_LIMIT: "命令输出超过限制，已停止执行。",
    Code.TOOL_COMMAND_FAILED: "命令退出失败，请查看受限输出。",
    Code.TOOL_REVIEW_FAILED: "模型自审查未通过校验，工具未执行。",
    Code.TOOL_LOOP_DETECTED: "工具反复失败或被拒绝，本轮已停止。",
    Code.FEATURE_REMOVED: "账户、余额与旧事件分析功能已移除，历史数据保留。",
    Code.REVIEW_EXPIRED: "工具审核已过期，请明确重试本轮。",
    Code.INVALID_INPUT: "请求或数据格式不正确。",
    Code.FORBIDDEN: "请求来源或权限不被允许。",
    Code.UNAUTHENTICATED: "客户端会话不可用，请重新连接后端。",
    Code.NOT_FOUND: "请求的记录不存在。",
    Code.BUSY: "已有任务正在执行，请稍后重试。",
    Code.DUPLICATE_CONNECTION: "这个模拟账户已经添加。",
    Code.INVALID_STATE: "当前记录状态不允许此操作。",
    Code.CREDENTIAL_UNAVAILABLE: "服务端安全凭据存储不可用。",
    Code.STORAGE_FAILED: "数据库操作失败。",
    Code.AUTH_EXPIRED: "模拟认证已过期，请重新连接。",
    Code.PERMISSION_DENIED: "模拟账户权限不足。",
    Code.NETWORK: "模拟网络暂时不可用。",
    Code.RATE_LIMITED: "模拟请求受到限流。",
    Code.SERVER_ERROR: "模拟服务暂时出错。",
    Code.TIMEOUT: "请求超时。",
    Code.CANCELLED: "任务已取消。",
    Code.INVALID_DATA: "执行数据未通过校验。",
    Code.INTERRUPTED: "上次执行被中断。",
    Code.INTERNAL: "操作未完成，请重试。",
    Code.LEASE_LOST: "工作进程已失去执行租约。",
    Code.REVISION_CONFLICT: "记录已更新，请刷新状态后重试。",
    Code.CONTEXT_TOO_LARGE: "本轮上下文超过大小限制。",
    Code.TOOL_BUDGET_EXCEEDED: "工具调用超过执行预算。",
    Code.TOOL_FAILED: "只读工具执行失败。",
    Code.MODEL_CONFIG_INVALID: "模型配置不可用，请检查服务端初始化或兼容导入。",
    Code.MODEL_NOT_CONFIGURED: "模型未启用或未配置可用的服务端 Key。",
    Code.MODEL_AUTH_FAILED: "模型认证或权限校验失败，请检查服务端 Key 和账户权限。",
    Code.MODEL_RATE_LIMITED: "模型服务限流或配额不足，请检查账户后重试。",
    Code.MODEL_UNAVAILABLE: "模型或网络暂时不可用，请稍后重试。",
    Code.MODEL_REQUEST_INVALID: "模型拒绝了请求，请检查模型、上下文和输出预算配置。",
    Code.MODEL_OUTPUT_LIMIT: "模型单次生成上限已用尽，仍未生成正文。",
    Code.MODEL_EMPTY_RESPONSE: "模型返回了空回复，请稍后重试。",
    Code.MODEL_REJECTED: "模型明确拒绝或过滤了本次请求。",
    Code.MODEL_RESPONSE_INVALID: "模型返回的数据未通过校验。",
    Code.MODEL_TIMEOUT: "模型请求超时，可稍后手动重试。",
    Code.MODEL_PROFILE_LIMIT: "模型配置已达到 32 项上限。",
    Code.SECRET_CHALLENGE_INVALID: "凭据提交已失效，请重新输入并保存。",
    Code.SECRET_SUBMISSION_INVALID: "凭据提交未通过校验，请重新输入并保存。",
    Code.SECRET_REVISION_CONFLICT: "凭据已被更改，请刷新状态后重新输入。",
    Code.SECRET_ENCRYPTION_UNAVAILABLE: "当前无法加密提交凭据，可使用服务端终端录入。",
}
RETRYABLE = frozenset(
    {Code.NETWORK, Code.TIMEOUT, Code.RATE_LIMITED, Code.SERVER_ERROR, Code.INTERRUPTED}
)


class AppError(Exception):
    def __init__(self, code: Code, retry_after: float = 0):
        self.code = code
        self.retry_after = min(max(retry_after, 0), 86400)
        super().__init__(code.value)


def status_code(code: Code) -> int:
    if code in {Code.POLICY_DENIED, Code.MODEL_REVIEW_DENIED, Code.NETWORK_DENIED}:
        return 403
    if code == Code.APPROVAL_REQUIRED:
        return 409
    if code in {Code.COMMAND_TOO_LONG, Code.RESOURCE_LIMIT}:
        return 422
    if code in {Code.SANDBOX_ERROR, Code.SANDBOX_CONFIG_INVALID}:
        return 503
    if code in {
        Code.DOCKER_NOT_INSTALLED,
        Code.DOCKER_NOT_RUNNING,
        Code.BASH_ISOLATION_UNSUPPORTED,
        Code.BASH_IMAGE_MISSING,
        Code.BASH_IMAGE_BUILD_FAILED,
        Code.BASH_STARTUP_FAILED,
    }:
        return 503
    if code == Code.FEATURE_REMOVED:
        return 410
    if code == Code.REVIEW_EXPIRED:
        return 409
    if code == Code.MODEL_PROFILE_LIMIT:
        return 409
    if code in {Code.SECRET_CHALLENGE_INVALID, Code.SECRET_REVISION_CONFLICT}:
        return 409
    if code == Code.SECRET_SUBMISSION_INVALID:
        return 422
    if code == Code.SECRET_ENCRYPTION_UNAVAILABLE:
        return 503
    if code == Code.MODEL_RATE_LIMITED:
        return 429
    if code == Code.MODEL_TIMEOUT:
        return 504
    if code in {Code.MODEL_CONFIG_INVALID, Code.MODEL_NOT_CONFIGURED, Code.MODEL_UNAVAILABLE}:
        return 503
    if code in {
        Code.MODEL_AUTH_FAILED,
        Code.MODEL_REQUEST_INVALID,
        Code.MODEL_REJECTED,
        Code.MODEL_OUTPUT_LIMIT,
        Code.MODEL_EMPTY_RESPONSE,
        Code.MODEL_RESPONSE_INVALID,
    }:
        return 502
    if code == Code.UNAUTHENTICATED:
        return 401
    if code in {Code.FORBIDDEN, Code.PERMISSION_DENIED}:
        return 403
    if code == Code.NOT_FOUND:
        return 404
    if code in {Code.BUSY, Code.DUPLICATE_CONNECTION, Code.INVALID_STATE, Code.REVISION_CONFLICT}:
        return 409
    if code in {
        Code.INVALID_INPUT,
        Code.INVALID_DATA,
        Code.CONTEXT_TOO_LARGE,
        Code.TOOL_BUDGET_EXCEEDED,
    }:
        return 422
    if code in {Code.CREDENTIAL_UNAVAILABLE, Code.LEASE_LOST, Code.BASH_UNAVAILABLE}:
        return 503
    return 500
