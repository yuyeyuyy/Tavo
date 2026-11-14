// Client DTO validation, public errors, and legacy data shapes used by imported history.
import { z } from 'zod';

export const idSchema = z.string().min(1).max(100).regex(/^[a-zA-Z0-9:_-]+$/);
export const timestampSchema = z.iso.datetime();
export const capabilitySchema = z.enum(['account', 'balance', 'usage', 'transaction', 'resource', 'event']);
export type Capability = z.infer<typeof capabilitySchema>;
export const connectorSchema = z.strictObject({
  id: idSchema,
  name: z.string().min(1).max(100),
  capabilities: z.array(capabilitySchema).max(6),
  permissions: z.array(z.literal('read:mock')).max(1),
  lastSyncAt: timestampSchema.nullable(),
});
export type ConnectorInfo = z.infer<typeof connectorSchema>;
export const accountSchema = z.strictObject({
  id: idSchema,
  connectorId: idSchema,
  providerAccountId: idSchema,
  name: z.string().min(1).max(100),
  balance: z.string().regex(/^\d{1,12}\.\d{2}$/),
  currency: z.enum(['USD', 'CNY']),
  updatedAt: timestampSchema,
  source: z.literal('mock'),
});
export type Account = z.infer<typeof accountSchema>;
export const analysisSchema = z.strictObject({
  eventId: idSchema,
  severity: z.enum(['info', 'warning']),
  summary: z.string().min(1).max(500),
  evidence: z.array(z.string().max(200)).min(1).max(10),
  recommendation: z.string().min(1).max(500),
  mode: z.literal('deterministic-mock'),
});
export type Analysis = z.infer<typeof analysisSchema>;
export const accountEventSchema = z.strictObject({
  id: idSchema,
  connectorId: idSchema,
  accountId: idSchema,
  providerEventId: idSchema,
  kind: z.literal('balance.observed'),
  balance: accountSchema.shape.balance,
  currency: accountSchema.shape.currency,
  occurredAt: timestampSchema,
  source: z.literal('mock'),
  analysis: analysisSchema.nullable(),
});
export type AccountEvent = z.infer<typeof accountEventSchema>;
export const taskSchema = z.strictObject({
  id: idSchema,
  connectorId: idSchema,
  status: z.enum(['running', 'succeeded', 'failed', 'interrupted']),
  startedAt: timestampSchema,
  finishedAt: timestampSchema.nullable(),
  errorCode: z.string().max(50).nullable(),
});
export type SyncTask = z.infer<typeof taskSchema>;
export const legacySnapshotSchema = z.strictObject({
  connectors: z.array(connectorSchema).max(100),
  accounts: z.array(accountSchema).max(100),
  events: z.array(accountEventSchema).max(100),
  tasks: z.array(taskSchema).max(50),
});
export type LegacySnapshot = z.infer<typeof legacySnapshotSchema>;

export const errorCodeSchema = z.enum([
  'INVALID_INPUT', 'FORBIDDEN', 'NOT_FOUND', 'BUSY', 'CONNECTOR_FAILED',
  'STORAGE_FAILED', 'AGENT_FAILED', 'INTERNAL', 'CREDENTIAL_UNAVAILABLE',
  'DUPLICATE_CONNECTION', 'INVALID_STATE', 'AUTH_EXPIRED', 'PERMISSION_DENIED',
  'NETWORK', 'RATE_LIMITED', 'SERVER_ERROR', 'TIMEOUT', 'CANCELLED', 'INVALID_DATA', 'INTERRUPTED',
  'UNAUTHENTICATED', 'BACKEND_UNAVAILABLE',
]);
export type ErrorCode = z.infer<typeof errorCodeSchema>;
export class AppError extends Error {
  constructor(public readonly code: ErrorCode) { super(code); this.name = 'AppError'; }
}
const errorMessages: Record<ErrorCode, string> = {
  INVALID_INPUT: '请求或数据格式不正确。', FORBIDDEN: '请求来源不被允许。',
  NOT_FOUND: '请求的数据不存在。', BUSY: '正在执行任务，请稍后重试。',
  CONNECTOR_FAILED: '模拟连接器同步失败；已保留上次成功的数据。',
  STORAGE_FAILED: '本地数据库操作失败。', AGENT_FAILED: '模拟分析执行失败。',
  INTERNAL: '操作未完成，请重试。', CREDENTIAL_UNAVAILABLE: '系统安全凭据存储不可用，操作已拒绝。',
  DUPLICATE_CONNECTION: '这个模拟账户已经添加。', INVALID_STATE: '当前账户状态不允许此操作。',
  AUTH_EXPIRED: '模拟认证已过期，请重新连接。', PERMISSION_DENIED: '模拟账户权限不足。',
  NETWORK: '模拟网络暂时不可用。', RATE_LIMITED: '模拟请求受到限流。', SERVER_ERROR: '模拟服务暂时出错。',
  TIMEOUT: '同步请求超时。', CANCELLED: '同步已取消。', INVALID_DATA: '连接器数据未通过校验。', INTERRUPTED: '上次执行被中断。',
  UNAUTHENTICATED: '客户端会话不可用，请重新连接后端。', BACKEND_UNAVAILABLE: '后端暂不可用，请检查独立服务和连接配置。',
};
export function publicError(error: unknown): { code: ErrorCode; message: string } {
  const code = error instanceof AppError ? error.code : 'INTERNAL';
  return { code, message: errorMessages[code] };
}
