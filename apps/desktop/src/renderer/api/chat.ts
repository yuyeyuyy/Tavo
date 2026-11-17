import { z } from 'zod';
const profileId = z.string().regex(/^[a-z][a-z0-9_-]{0,63}$/);
const provider = z.enum(['openai', 'anthropic', 'gemini', 'deepseek', 'qwen', 'kimi', 'zhipu', 'openrouter', 'xai']);
export const modelOptionSchema = z.strictObject({ id: profileId, label: z.string().min(1).max(80), provider, model: z.string().min(1).max(160), ready: z.boolean(), credentialRevision: z.number().int().nonnegative().default(0) });
export const modelCatalogSchema = z.strictObject({ items: z.array(modelOptionSchema).max(32), defaultProfileId: profileId.nullable() });
export const chatReplySchema = z.strictObject({ profileId, provider, model: z.string().min(1).max(160), message: z.strictObject({ role: z.literal('assistant'), content: z.string().trim().min(1).max(16000) }), finishReason: z.enum(['stop', 'length']), truncated: z.boolean(), usage: z.strictObject({ inputTokens: z.number().int().nonnegative().nullable(), outputTokens: z.number().int().nonnegative().nullable() }).nullable() });
export type ModelOption = z.infer<typeof modelOptionSchema>;
