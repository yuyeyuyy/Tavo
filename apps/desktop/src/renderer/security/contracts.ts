import { z } from 'zod';
const identity = z.string().regex(/^[a-zA-Z0-9_-]{32}$/);
const resource = z.string().regex(/^[a-z][a-z0-9_-]{0,63}$/);
const revision = z.number().int().nonnegative();
export const challengeSchema = z.strictObject({ version: z.literal(1), challengeId: identity, expiresAt: z.string().datetime(), purpose: z.enum(['model.api_key', 'sandbox.api_key']), resourceId: resource, credentialRevision: revision, kid: identity, publicJwk: z.strictObject({ kty: z.literal('RSA'), n: z.string().regex(/^[A-Za-z0-9_-]{512}$/), e: z.literal('AQAB'), alg: z.literal('RSA-OAEP-256'), ext: z.literal(true), keyOps: z.tuple([z.literal('wrapKey')]) }) });
export const submissionAckSchema = z.strictObject({ resourceId: resource, credentialRevision: revision, ready: z.literal(true) });
export type SecretChallenge = z.infer<typeof challengeSchema>;
export interface SecretTarget { purpose: 'model.api_key' | 'sandbox.api_key'; resourceId: string; expectedRevision: number }
