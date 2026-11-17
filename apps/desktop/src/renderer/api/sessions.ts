import { z } from 'zod';
import { idSchema, timestampSchema } from '@tavo/shared';
const text = (maximum: number) => z.string().min(1).max(maximum * 2).refine(value => [...value].length <= maximum);
const reviewMode = z.enum(['full_access', 'auto_review', 'request_approval', 'auto_readonly', 'ask', 'disabled', 'auto_sandbox']);
const revision = z.number().int().nonnegative();
const usage = z.strictObject({ inputTokens: z.number().int().nonnegative().nullable(), outputTokens: z.number().int().nonnegative().nullable() }).nullable();
export const sessionCreateSchema = z.strictObject({ profileId: z.string().regex(/^[a-z][a-z0-9_-]{0,63}$/), reviewMode, bashEnabled: z.boolean().optional() });
export const sessionSchema = z.strictObject({ id: idSchema, title: text(80), profileId: sessionCreateSchema.shape.profileId, reviewMode, bashEnabled: z.boolean().default(false), executionScope:z.enum(['sandbox_only','host_and_sandbox']).default('sandbox_only'), revision, createdAt: timestampSchema, updatedAt: timestampSchema, activeRunId: idSchema.nullable(), latestRunId: idSchema.nullable() });
export const sessionPageSchema = z.strictObject({ items: z.array(sessionSchema).max(100), nextCursor: z.string().nullable() });
export const decisionSchema = z.strictObject({ id: idSchema, toolCallId: idSchema, revision, action: z.enum(['allow', 'deny', 'ask']), source: z.enum(['policy', 'human', 'model', 'mock']), reasonCode: z.string().max(100), summary: text(240).nullable().optional(), policyVersion: z.string().max(50), createdAt: timestampSchema });
export const toolCallSchema = z.strictObject({ id: idSchema, runId: idSchema, round: z.number().int().positive(), name: z.string().min(1).max(128), arguments: z.record(z.string(), z.json()).nullable(), status: z.enum(['proposed', 'waiting_review', 'approved', 'running', 'succeeded', 'failed', 'denied', 'cancelled']), reviewRevision: revision, expiresAt: timestampSchema.nullable(), attempts: revision, decisions: z.array(decisionSchema).max(16) });
const resultSchema = z.strictObject({ id: idSchema, toolCallId: idSchema, status: z.enum(['succeeded', 'failed', 'denied', 'cancelled']), output: z.record(z.string(), z.json()), errorCode: z.string().nullable(), createdAt: timestampSchema });
const base = { id: idSchema, sessionId: idSchema, runId: idSchema.nullable(), sequence: z.number().int().positive(), createdAt: timestampSchema };
const user = z.strictObject({ ...base, kind: z.literal('user'), content: text(4000) });
const assistant = z.strictObject({ ...base, kind: z.literal('assistant'), content: text(16000), final: z.boolean(), truncated: z.boolean(), usage });
export const messageSchema = z.discriminatedUnion('kind', [user, assistant, z.strictObject({ ...base, kind: z.literal('tool_call'), call: toolCallSchema }), z.strictObject({ ...base, kind: z.literal('tool_result'), result: resultSchema })]);
export const messagePageSchema = z.strictObject({ items: z.array(messageSchema).max(100), hasMore: z.boolean(), nextSequence: z.number().int().positive().nullable() });
const context = z.strictObject({ policyVersion: z.string(), fromSequence: z.number().int().positive(), toSequence: z.number().int().positive(), droppedTurns: revision, contextBytes: z.number().int().nonnegative().max(1048576), toolNames: z.array(z.string()).max(6) });
export const runSchema = z.strictObject({ id: idSchema, sessionId: idSchema, userMessageId: idSchema, previousRunId: idSchema.nullable(), status: z.enum(['pending', 'running', 'waiting_review', 'succeeded', 'failed', 'cancelled', 'interrupted']), step: z.string(), rounds: revision, toolCount: revision, revision, createdAt: timestampSchema, startedAt: timestampSchema.nullable(), finishedAt: timestampSchema.nullable(), errorCode: z.string().nullable(), context: context.nullable(), toolCalls: z.array(toolCallSchema).max(64) });
export const messageWriteSchema = z.strictObject({ clientMessageId: idSchema, content: z.string().trim().min(1).max(4000), expectedRevision: revision });
export const decisionWriteSchema = z.strictObject({ action: z.enum(['approve', 'reject']), expectedRevision: z.number().int().positive() });
export const acceptedSchema = z.strictObject({ message: user, run: runSchema, session: sessionSchema });
export type SessionCreate = z.infer<typeof sessionCreateSchema>;
export type MessageWrite = z.infer<typeof messageWriteSchema>;
export type DecisionWrite = z.infer<typeof decisionWriteSchema>;
export type AgentSession = z.infer<typeof sessionSchema>;
export type AgentRun = z.infer<typeof runSchema>;
export type AgentMessage = z.infer<typeof messageSchema>;
export type AgentToolCall = z.infer<typeof toolCallSchema>;

export const agentRuntimeSchema = z.strictObject({hostBash:z.strictObject({enabled:z.boolean(),available:z.boolean(),reasonCode:z.string().nullable(),executionTarget:z.literal('backend-host')}).optional(),pool:z.strictObject({capacity:revision,activeRuns:revision,occupiedSlots:revision,cleanupSlots:revision}),bash:z.strictObject({enabled:z.boolean(),available:z.boolean(),reasonCode:z.string().nullable(),workspacePolicy:z.literal('session-volume'),externalNetwork:z.boolean(),networkPolicy:z.enum(['none','proxy']).default('none')})});

export const networkTargetsSchema = z.strictObject({mode:z.enum(['none','proxy']),items:z.array(z.strictObject({id:idSchema,hostname:z.string().max(253),capability:z.string().max(50),methods:z.array(z.string()).max(6),paths:z.array(z.string()).max(32),credentialRequired:z.boolean(),credentialRevision:revision,ready:z.boolean()})).max(32)});

export const approvalPreviewSchema = z.strictObject({kind:z.enum(['network','workspace_commit']),description:z.string().max(400),digest:z.string().regex(/^[a-f0-9]{64}$/),details:z.record(z.string(),z.json())});
