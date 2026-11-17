// Shared public errors and scalar validation. Account/amount business contracts are retired.
import { z } from 'zod';
export const idSchema = z.string().min(1).max(120).regex(/^[a-zA-Z0-9:_-]+$/);
export const timestampSchema = z.iso.datetime();

export const errorCodeSchema = z.enum([
'POLICY_DENIED','MODEL_REVIEW_DENIED','APPROVAL_REQUIRED','COMMAND_TOO_LONG','RESOURCE_LIMIT','NETWORK_DENIED','SANDBOX_ERROR','HOST_UNAVAILABLE','EXECUTION_ERROR','SANDBOX_CONFIG_INVALID',
'DOCKER_NOT_INSTALLED','DOCKER_NOT_RUNNING','BASH_ISOLATION_UNSUPPORTED','BASH_IMAGE_MISSING','BASH_IMAGE_BUILD_FAILED','BASH_STARTUP_FAILED',
  'BASH_UNAVAILABLE','TOOL_EXECUTION_UNKNOWN','TOOL_OUTPUT_LIMIT','TOOL_COMMAND_FAILED','TOOL_REVIEW_FAILED','TOOL_LOOP_DETECTED',
  'FEATURE_REMOVED', 'REVIEW_EXPIRED',
  'INVALID_INPUT', 'FORBIDDEN', 'NOT_FOUND', 'BUSY', 'CONNECTOR_FAILED',
  'STORAGE_FAILED', 'AGENT_FAILED', 'INTERNAL', 'CREDENTIAL_UNAVAILABLE',
  'DUPLICATE_CONNECTION', 'INVALID_STATE', 'AUTH_EXPIRED', 'PERMISSION_DENIED',
  'NETWORK', 'RATE_LIMITED', 'SERVER_ERROR', 'TIMEOUT', 'CANCELLED', 'INVALID_DATA', 'INTERRUPTED',
  'UNAUTHENTICATED', 'BACKEND_UNAVAILABLE', 'REVISION_CONFLICT', 'CONTEXT_TOO_LARGE', 'TOOL_BUDGET_EXCEEDED', 'TOOL_FAILED',
  'MODEL_CONFIG_INVALID', 'MODEL_NOT_CONFIGURED', 'MODEL_AUTH_FAILED', 'MODEL_RATE_LIMITED', 'MODEL_UNAVAILABLE', 'MODEL_REQUEST_INVALID', 'MODEL_REJECTED', 'MODEL_OUTPUT_LIMIT', 'MODEL_EMPTY_RESPONSE', 'MODEL_RESPONSE_INVALID', 'MODEL_TIMEOUT', 'MODEL_PROFILE_LIMIT',
  'SECRET_CHALLENGE_INVALID', 'SECRET_SUBMISSION_INVALID', 'SECRET_REVISION_CONFLICT', 'SECRET_ENCRYPTION_UNAVAILABLE',
]);
export type ErrorCode = z.infer<typeof errorCodeSchema>;
export class AppError extends Error {
  constructor(public readonly code: ErrorCode) { super(code); this.name = 'AppError'; }
}
const errorMessages: Record<ErrorCode, string> = {
  POLICY_DENIED: '操作超出已授权执行范围，规则拒绝执行。', MODEL_REVIEW_DENIED: '模型风险复核拒绝执行，请查看审核摘要。', APPROVAL_REQUIRED: '具体操作需要用户批准，尚未执行或提交。', COMMAND_TOO_LONG: '命令超过 UTF-8 字节上限，请分块写入脚本文件后执行。', RESOURCE_LIMIT: '执行触及内存、进程、文件或工作区资源限制。', NETWORK_DENIED: '网络目标或操作未获授权，或属于受保护地址。', SANDBOX_ERROR: '沙箱启动、状态核验或清理失败。', HOST_UNAVAILABLE: '宿主机 Bash 已关闭，或当前平台不支持。', EXECUTION_ERROR: '命令执行失败，请根据退出码和输出修复。', SANDBOX_CONFIG_INVALID: '沙箱配置不合法，请检查资源与网络策略。',

  DOCKER_NOT_INSTALLED: '未找到 Docker 命令，请安装 Docker 后重新启动；后端已停止。',
  DOCKER_NOT_RUNNING: 'Docker 服务未启动或无法连接，请启动 Docker Desktop / Docker Engine 后重试；后端已停止。',
  BASH_ISOLATION_UNSUPPORTED: 'Docker 不支持所需的沙箱隔离或资源限制；后端已停止。',
  BASH_IMAGE_MISSING: '找不到配置的 Bash 执行镜像，请准备该镜像后重试；后端已停止。',
  BASH_IMAGE_BUILD_FAILED: '默认 Bash 执行镜像构建失败或超时，请检查 Docker、网络和镜像构建文件；后端已停止。',
  BASH_STARTUP_FAILED: 'Bash 沙箱预热或清理失败，请检查 Docker 和执行镜像；后端已停止。',

  BASH_UNAVAILABLE:'沙箱不可用，请检查服务端 Docker 和执行镜像。', TOOL_EXECUTION_UNKNOWN:'命令可能已产生实际变更，结果未知；请先核实后再决定是否重试。', TOOL_OUTPUT_LIMIT:'命令输出超限，已停止执行。', TOOL_COMMAND_FAILED:'命令退出失败，请查看工具输出。', TOOL_REVIEW_FAILED:'模型自审查未通过校验，工具未执行。', TOOL_LOOP_DETECTED:'工具反复失败或被拒绝，本轮已停止。',
  FEATURE_REMOVED: '账户、余额与旧事件分析功能已移除，历史数据保留。', REVIEW_EXPIRED: '工具审核已过期，请明确重试本轮。',
  INVALID_INPUT: '请求或数据格式不正确。', FORBIDDEN: '请求来源不被允许。',
  NOT_FOUND: '请求的数据不存在。', BUSY: '正在执行任务，请稍后重试。',
  CONNECTOR_FAILED: '模拟连接器同步失败；已保留上次成功的数据。',
  STORAGE_FAILED: '本地数据库操作失败。', AGENT_FAILED: '模拟分析执行失败。',
  INTERNAL: '操作未完成，请重试。', CREDENTIAL_UNAVAILABLE: '系统安全凭据存储不可用，操作已拒绝。',
  DUPLICATE_CONNECTION: '这个模拟账户已经添加。', INVALID_STATE: '当前记录状态不允许此操作。',
  AUTH_EXPIRED: '模拟认证已过期，请重新连接。', PERMISSION_DENIED: '模拟账户权限不足。',
  NETWORK: '模拟网络暂时不可用。', RATE_LIMITED: '模拟请求受到限流。', SERVER_ERROR: '模拟服务暂时出错。',
  TIMEOUT: '操作等待超时。', CANCELLED: '操作已取消。', INVALID_DATA: '执行数据未通过校验。', INTERRUPTED: '上次执行被中断。',
  REVISION_CONFLICT: '记录已更新，请刷新状态后重试。', CONTEXT_TOO_LARGE: '本轮上下文超过大小限制。', TOOL_BUDGET_EXCEEDED: '工具调用超过执行预算。', TOOL_FAILED: '只读工具执行失败。',
  MODEL_CONFIG_INVALID: '模型配置不可用，请检查服务端初始化或兼容导入。', MODEL_NOT_CONFIGURED: '模型未启用或未配置可用的服务端 Key。', MODEL_AUTH_FAILED: '模型认证或权限校验失败，请检查服务端 Key 和账户权限。', MODEL_RATE_LIMITED: '模型服务限流或配额不足，请检查账户后重试。', MODEL_UNAVAILABLE: '模型或网络暂时不可用，请稍后重试。', MODEL_REQUEST_INVALID: '模型拒绝了请求，请检查模型、上下文和输出预算配置。', MODEL_REJECTED: '模型明确拒绝或过滤了本次请求。', MODEL_OUTPUT_LIMIT: '模型单次生成上限已用尽，仍未生成正文。', MODEL_EMPTY_RESPONSE: '模型返回了空回复，请稍后重试。', MODEL_RESPONSE_INVALID: '模型返回的数据未通过校验。', MODEL_TIMEOUT: '模型请求超时，可稍后手动重试。',
  MODEL_PROFILE_LIMIT: '模型配置已达到 32 项上限。',
  SECRET_CHALLENGE_INVALID: '凭据提交已失效，请重新输入并保存。', SECRET_SUBMISSION_INVALID: '凭据提交未通过校验，请重新输入并保存。', SECRET_REVISION_CONFLICT: '凭据已被更改，请刷新状态后重新输入。', SECRET_ENCRYPTION_UNAVAILABLE: '当前无法加密提交凭据，可使用服务端终端录入。',
  UNAUTHENTICATED: '客户端会话不可用，请重新连接后端。', BACKEND_UNAVAILABLE: '后端暂不可用，请检查独立服务和连接配置。',
};
export function publicError(error: unknown): { code: ErrorCode; message: string } {
  const code = error instanceof AppError ? error.code : 'INTERNAL';
  return { code, message: errorMessages[code] };
}
