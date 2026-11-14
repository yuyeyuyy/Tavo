from enum import StrEnum


class Code(StrEnum):
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


MESSAGES = {
    Code.INVALID_INPUT: "请求或数据格式不正确。",
    Code.FORBIDDEN: "请求来源或权限不被允许。",
    Code.UNAUTHENTICATED: "客户端会话不可用，请重新连接后端。",
    Code.NOT_FOUND: "请求的记录不存在。",
    Code.BUSY: "已有任务正在执行，请稍后重试。",
    Code.DUPLICATE_CONNECTION: "这个模拟账户已经添加。",
    Code.INVALID_STATE: "当前账户状态不允许此操作。",
    Code.CREDENTIAL_UNAVAILABLE: "服务端安全凭据存储不可用。",
    Code.STORAGE_FAILED: "数据库操作失败。",
    Code.AUTH_EXPIRED: "模拟认证已过期，请重新连接。",
    Code.PERMISSION_DENIED: "模拟账户权限不足。",
    Code.NETWORK: "模拟网络暂时不可用。",
    Code.RATE_LIMITED: "模拟请求受到限流。",
    Code.SERVER_ERROR: "模拟服务暂时出错。",
    Code.TIMEOUT: "请求超时。",
    Code.CANCELLED: "任务已取消。",
    Code.INVALID_DATA: "连接器数据未通过校验。",
    Code.INTERRUPTED: "上次执行被中断。",
    Code.INTERNAL: "操作未完成，请重试。",
    Code.LEASE_LOST: "工作进程已失去执行租约。",
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
    if code == Code.UNAUTHENTICATED:
        return 401
    if code in {Code.FORBIDDEN, Code.PERMISSION_DENIED}:
        return 403
    if code == Code.NOT_FOUND:
        return 404
    if code in {Code.BUSY, Code.DUPLICATE_CONNECTION, Code.INVALID_STATE}:
        return 409
    if code in {Code.INVALID_INPUT, Code.INVALID_DATA}:
        return 422
    if code in {Code.CREDENTIAL_UNAVAILABLE, Code.LEASE_LOST}:
        return 503
    return 500
