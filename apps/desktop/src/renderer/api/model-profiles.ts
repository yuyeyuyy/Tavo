import { z } from 'zod';
import { modelOptionSchema } from './chat';

export const providerSchema = z.enum(['openai', 'anthropic', 'gemini', 'deepseek', 'qwen', 'kimi', 'zhipu', 'openrouter', 'xai']);
export const providerListSchema = z.strictObject({ items: z.array(z.strictObject({ id: providerSchema, label: z.string().min(1).max(80), modelHint: z.string().min(1).max(160) })).max(9) });
export const modelProfileCreateSchema = z.strictObject({ provider: providerSchema, model: z.string().min(1).max(160).regex(/^[a-zA-Z0-9._:/-]+$/).refine((value) => value !== 'SET_MODEL_ID'), label: z.string().trim().min(1).max(80).optional() });
export const modelProfileSchema = modelOptionSchema.extend({ enabled: z.boolean() });
export type ModelProfileCreate = z.infer<typeof modelProfileCreateSchema>;
