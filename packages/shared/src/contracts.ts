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
export const syncResultSchema = z.strictObject({
  accounts: z.array(accountSchema).min(1).max(100),
  events: z.array(accountEventSchema).min(1).max(100),
});
export type SyncResult = z.infer<typeof syncResultSchema>;
export const taskSchema = z.strictObject({
  id: idSchema,
  connectorId: idSchema,
  status: z.enum(['running', 'succeeded', 'failed', 'interrupted']),
  startedAt: timestampSchema,
  finishedAt: timestampSchema.nullable(),
  errorCode: z.string().max(50).nullable(),
});
export type SyncTask = z.infer<typeof taskSchema>;
export const snapshotSchema = z.strictObject({
  connectors: z.array(connectorSchema).max(100),
  accounts: z.array(accountSchema).max(100),
  events: z.array(accountEventSchema).max(100),
  tasks: z.array(taskSchema).max(50),
});
export type Snapshot = z.infer<typeof snapshotSchema>;

// 权限只由宿主装配决定；来自 Renderer 的数据不能扩展操作或工具权限。
export const requestSchemas = {
  snapshot: z.strictObject({}),
  sync: z.strictObject({ connectorId: z.literal('mock') }),
  analyze: z.strictObject({ eventId: idSchema }),
};
export type Operation = keyof typeof requestSchemas;
export const errorCodeSchema = z.enum([
  'INVALID_INPUT', 'FORBIDDEN', 'NOT_FOUND', 'BUSY', 'CONNECTOR_FAILED',
  'STORAGE_FAILED', 'AGENT_FAILED', 'INTERNAL', 'CREDENTIAL_UNAVAILABLE',
  'DUPLICATE_CONNECTION', 'INVALID_STATE', 'AUTH_EXPIRED', 'PERMISSION_DENIED',
  'NETWORK', 'RATE_LIMITED', 'SERVER_ERROR', 'TIMEOUT', 'CANCELLED', 'INVALID_DATA', 'INTERRUPTED',
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
};
export function publicError(error: unknown): { code: ErrorCode; message: string } {
  const code = error instanceof AppError ? error.code : 'INTERNAL';
  return { code, message: errorMessages[code] };
}
export const responseSchema = z.discriminatedUnion('ok', [
  z.strictObject({ ok: z.literal(true), data: snapshotSchema }),
  z.strictObject({ ok: z.literal(false), error: z.strictObject({ code: errorCodeSchema, message: z.string().max(200) }) }),
]);
export type Response = z.infer<typeof responseSchema>;
export interface DesktopAPI {
  snapshot(): Promise<Response>;
  sync(input: { connectorId: 'mock' }): Promise<Response>;
  analyze(input: { eventId: string }): Promise<Response>;
}

// 业务端口使用同步事务；网络连接器和 Agent 则保持异步。
export interface Storage {
  registerConnector(info: ConnectorInfo): void;
  snapshot(): Snapshot;
  startTask(task: SyncTask): void;
  commitSync(taskId: string, connectorId: string, result: SyncResult, at: string): void;
  failTask(taskId: string, code: ErrorCode, at: string): void;
  getEvent(id: string): AccountEvent | undefined;
  saveAnalysis(analysis: Analysis): void;
  deleteAccount(id: string): void;
  recoverInterruptedTasks(at: string): void;
  close(): void;
}
export interface AgentRuntime { analyze(event: AccountEvent): Promise<Analysis> }
export interface CredentialStore {
  set(reference: string, secret: string): Promise<void>;
  get(reference: string): Promise<string>;
  delete(reference: string): Promise<void>;
}
// Fail closed：任何操作都明确报错，不提供明文或伪成功实现。
export class UnavailableCredentialStore implements CredentialStore {
  async set(_reference: string, _secret: string): Promise<void> { throw new AppError('CREDENTIAL_UNAVAILABLE'); }
  async get(_reference: string): Promise<string> { throw new AppError('CREDENTIAL_UNAVAILABLE'); }
  async delete(_reference: string): Promise<void> { throw new AppError('CREDENTIAL_UNAVAILABLE'); }
}
