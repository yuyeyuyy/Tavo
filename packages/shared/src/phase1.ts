import { z } from 'zod';
import { idSchema, timestampSchema, errorCodeSchema, analysisSchema, snapshotSchema as legacySnapshotSchema, type CredentialStore, type ErrorCode } from './contracts';

export const connectionStatusSchema = z.enum(['disconnected', 'connecting', 'connected', 'expired', 'error']);
export type ConnectionStatus = z.infer<typeof connectionStatusSchema>;
export const mockScenarioSchema = z.enum(['success', 'network_once', 'rate_limit_once', 'server_once', 'timeout', 'auth', 'forbidden', 'invalid']);
export const mockConfigSchema = z.strictObject({
  balance: z.string().regex(/^\d{1,12}\.\d{2}$/), currency: z.enum(['USD', 'CNY']),
  usageUnits: z.number().int().min(0).max(1_000_000_000), resourceStatus: z.enum(['healthy', 'degraded']),
  scenario: mockScenarioSchema,
});
export type MockConfig = z.infer<typeof mockConfigSchema>;
const connectionObject = z.strictObject({
  id: idSchema, connectorId: idSchema, externalAccountId: idSchema,
  displayName: z.string().min(1).max(100), status: connectionStatusSchema,
  credentialConfigured: z.boolean(), lastSyncAt: timestampSchema.nullable(),
  nextSyncAt: timestampSchema.nullable(), errorCode: errorCodeSchema.nullable(), createdAt: timestampSchema,
  mock: mockConfigSchema,
});
function connectionInvariant(value: z.infer<typeof connectionObject>, ctx: z.RefinementCtx) {
  if ((value.status === 'connected' && (!value.credentialConfigured || value.errorCode !== null)) ||
    (['disconnected', 'connecting'].includes(value.status) && value.credentialConfigured) ||
    (value.status === 'connecting' && value.errorCode !== null) ||
    (value.status === 'expired' && value.errorCode !== 'AUTH_EXPIRED') ||
    (value.status === 'error' && value.errorCode === null)) ctx.addIssue({ code: 'custom', message: '账户状态与凭据或错误信息不一致' });
}
export const connectionSchema = connectionObject.superRefine(connectionInvariant);
export type Connection = z.infer<typeof connectionSchema>;
export const connectionRecordSchema = connectionObject.extend({
  credentialRef: idSchema, cursor: z.string().max(512).nullable(), revision: z.number().int().positive(),
  cleanupPending: z.boolean(), deleteRequested: z.boolean(), deletedAt: timestampSchema.nullable(),
}).superRefine(connectionInvariant);
export type ConnectionRecord = z.infer<typeof connectionRecordSchema>;
export const connectionDataSchema = z.strictObject({
  connectionId: idSchema, providerAccountId: idSchema,
  balance: mockConfigSchema.shape.balance, currency: mockConfigSchema.shape.currency,
  usageUnits: mockConfigSchema.shape.usageUnits, resourceStatus: mockConfigSchema.shape.resourceStatus,
  revision: z.number().int().positive(), updatedAt: timestampSchema, source: z.literal('mock'),
});
export type ConnectionData = z.infer<typeof connectionDataSchema>;
export const connectionSyncResultSchema = z.strictObject({ data: connectionDataSchema.nullable(), nextCursor: z.string().min(1).max(512) });
export type ConnectionSyncResult = z.infer<typeof connectionSyncResultSchema>;
export const syncJobSchema = z.strictObject({
  id: idSchema, connectionId: idSchema, status: z.enum(['pending', 'running', 'succeeded', 'failed', 'cancelled']),
  trigger: z.enum(['manual', 'scheduled']), attempts: z.number().int().min(0), retryCount: z.number().int().min(0),
  maxAttempts: z.number().int().min(1).max(5), createdAt: timestampSchema,
  startedAt: timestampSchema.nullable(), finishedAt: timestampSchema.nullable(), nextAttemptAt: timestampSchema,
  errorCode: errorCodeSchema.nullable(),
});
export type SyncJob = z.infer<typeof syncJobSchema>;
export const eventTypeSchema = z.enum(['account.connected', 'account.disconnected', 'sync.started', 'sync.completed', 'sync.failed', 'data.changed', 'sync.retry_scheduled', 'sync.cancelled']);
export const changedFieldSchema = z.enum(['balance', 'currency', 'usageUnits', 'resourceStatus']);
export const eventPayloadSchema = z.strictObject({
  errorCode: errorCodeSchema.optional(), attempt: z.number().int().min(0).optional(), nextAttemptAt: timestampSchema.optional(),
  changedFields: z.array(changedFieldSchema).max(4).optional(),
  previous: connectionDataSchema.nullable().optional(), current: connectionDataSchema.optional(),
});
export const businessEventSchema = z.strictObject({
  eventId: idSchema, sequence: z.number().int().min(1), eventType: eventTypeSchema,
  sourceId: idSchema, accountId: idSchema, occurredAt: timestampSchema,
  payload: eventPayloadSchema, correlationId: idSchema, analysis: analysisSchema.nullable(),
}).superRefine((event, ctx) => {
  if (event.eventType === 'data.changed' && (!event.payload.current || event.payload.current.connectionId !== event.accountId || !event.payload.changedFields?.length)) ctx.addIssue({ code: 'custom', message: '变化事件缺少数据与变化字段' });
  if (['sync.failed', 'sync.retry_scheduled', 'sync.cancelled'].includes(event.eventType) && !event.payload.errorCode) ctx.addIssue({ code: 'custom', message: '失败事件缺少错误码' });
  if (event.eventType === 'sync.retry_scheduled' && !event.payload.nextAttemptAt) ctx.addIssue({ code: 'custom', message: '重试事件缺少时间' });
  if (event.analysis && (event.analysis.eventId !== event.eventId || event.eventType !== 'data.changed')) ctx.addIssue({ code: 'custom', message: '分析与来源事件不一致' });
});
export type BusinessEvent = z.infer<typeof businessEventSchema>;
export type NewBusinessEvent = Omit<BusinessEvent, 'sequence' | 'analysis'> & { dedupeKey: string };
export interface EventRepository {
  append(event: NewBusinessEvent): BusinessEvent | undefined;
  list(accountId?: string): BusinessEvent[];
  get(eventId: string): BusinessEvent | undefined;
}
export const settingsSchema = z.strictObject({ autoSync: z.boolean(), syncIntervalSeconds: z.number().int().min(10).max(86400), startMinimized: z.boolean() });
export type Settings = z.infer<typeof settingsSchema>;
export const defaultSettings: Settings = { autoSync: false, syncIntervalSeconds: 300, startMinimized: false };
export const securityStatusSchema = z.strictObject({ available: z.boolean(), backend: z.string().max(80), dataLocation: z.string().max(1024) });
export type SecurityStatus = z.infer<typeof securityStatusSchema>;
export const snapshotSchema = legacySnapshotSchema.extend({
  connections: z.array(connectionSchema).max(200).default([]), data: z.array(connectionDataSchema).max(200).default([]),
  jobs: z.array(syncJobSchema).max(200).default([]), businessEvents: z.array(businessEventSchema).max(300).default([]),
  settings: settingsSchema.default(defaultSettings),
  security: securityStatusSchema.default({ available: false, backend: 'unavailable', dataLocation: '本地用户数据目录' }),
});
export type Snapshot = z.infer<typeof snapshotSchema>;
export const responseSchema = z.discriminatedUnion('ok', [
  z.strictObject({ ok: z.literal(true), data: snapshotSchema }),
  z.strictObject({ ok: z.literal(false), error: z.strictObject({ code: errorCodeSchema, message: z.string().max(200) }) }),
]);
export type Response = z.infer<typeof responseSchema>;
export const workspaceRequestSchemas = {
  addConnection: z.strictObject({ connectorId: z.literal('mock'), externalAccountId: idSchema, displayName: z.string().trim().min(1).max(100) }),
  disconnect: z.strictObject({ connectionId: idSchema }), removeConnection: z.strictObject({ connectionId: idSchema }),
  reconnect: z.strictObject({ connectionId: idSchema }), syncConnection: z.strictObject({ connectionId: idSchema }),
  cancelSync: z.strictObject({ connectionId: idSchema }),
  configureMock: z.strictObject({ connectionId: idSchema, config: mockConfigSchema }),
  updateSettings: settingsSchema,
};
export type WorkspaceOperation = keyof typeof workspaceRequestSchemas;
export type WorkspaceInputs = { [K in WorkspaceOperation]: z.infer<(typeof workspaceRequestSchemas)[K]> };
export type WorkspaceAPI = { [K in WorkspaceOperation]: (input: WorkspaceInputs[K]) => Promise<Response> } & {
  snapshot(): Promise<Response>; analyze(input: { eventId: string }): Promise<Response>;
};
export interface CiphertextRepository {
  read(reference: string): Uint8Array | undefined;
  write(reference: string, ciphertext: Uint8Array): void;
  replace(reference: string, previous: Uint8Array, ciphertext: Uint8Array): boolean;
  delete(reference: string): void;
}
export interface SecureCredentialStore extends CredentialStore { status(): Promise<{ available: boolean; backend: string }> }
export interface CredentialAccess {
  use<T>(connection: ConnectionRecord, operation: (secret: string) => Promise<T>): Promise<T>;
}
export interface WorkspaceStorage {
  readonly events: EventRepository;
  readonly ciphertexts: CiphertextRepository;
  getConnection(id: string): ConnectionRecord | undefined;
  listConnections(includeDeleted?: boolean): ConnectionRecord[];
  addConnection(connection: ConnectionRecord): void;
  setConnecting(id: string): void;
  finishConnecting(id: string, at: string): BusinessEvent[];
  connectionError(id: string, code: ErrorCode): void;
  beginDisconnect(id: string, remove: boolean, at: string): BusinessEvent[];
  finishCleanup(id: string, at: string): void;
  configureMock(id: string, config: MockConfig): void;
  getSettings(): Settings;
  updateSettings(settings: Settings, at: string): void;
  getData(id: string): ConnectionData | undefined;
  enqueue(job: SyncJob): void;
  getJob(id: string): SyncJob | undefined;
  dueJobs(at: string): SyncJob[];
  startJob(id: string, at: string): BusinessEvent[];
  commitJob(id: string, result: ConnectionSyncResult, at: string): BusinessEvent[];
  failJob(id: string, code: ErrorCode, nextAttemptAt: string | null, at: string): BusinessEvent[];
  cancelJobs(connectionId: string, at: string): BusinessEvent[];
  recoverJobs(at: string): BusinessEvent[];
  saveEventAnalysis(eventId: string, analysis: z.infer<typeof analysisSchema>): void;
}
