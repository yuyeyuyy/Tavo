import { AppError } from '@tavo/shared';
import { api, type APIClient } from '../api/client';
import { challengeSchema, type SecretChallenge, type SecretTarget } from './contracts';

const encoded = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');

// A deliberately limited JWE Compact serializer. No raw AES key export, algorithm choice or key URLs.
// The supplied plaintext buffer is consumed and cleared on every exit path.
export async function encryptSecret(plaintext: Uint8Array<ArrayBuffer>, challenge: SecretChallenge, target: SecretTarget, engine: Crypto = globalThis.crypto) {
  try {
    if (!engine?.subtle) throw new AppError('SECRET_ENCRYPTION_UNAVAILABLE');
    const value = challengeSchema.parse(challenge);
    if (value.purpose !== target.purpose || value.resourceId !== target.resourceId || value.credentialRevision !== target.expectedRevision) throw new AppError('SECRET_CHALLENGE_INVALID');
    if (plaintext.byteLength < 1 || plaintext.byteLength > 8192) throw new AppError('INVALID_INPUT');
    const { keyOps, ...jwk } = value.publicJwk;
    const recipient = await engine.subtle.importKey('jwk', { ...jwk, key_ops: keyOps }, { name: 'RSA-OAEP', hash: 'SHA-256' }, false, ['wrapKey']);
    const key = await engine.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt']);
    const protectedHeader = encoded(new TextEncoder().encode(JSON.stringify({ v: 1, typ: 'tavo-secret+jwe', alg: 'RSA-OAEP-256', enc: 'A256GCM', kid: value.kid, challengeId: value.challengeId, purpose: value.purpose, resourceId: value.resourceId })));
    const iv = engine.getRandomValues(new Uint8Array(12));
    const ciphertext = new Uint8Array(await engine.subtle.encrypt({ name: 'AES-GCM', iv, tagLength: 128, additionalData: new TextEncoder().encode(protectedHeader) }, key, plaintext));
    const wrapped = new Uint8Array(await engine.subtle.wrapKey('raw', key, recipient, { name: 'RSA-OAEP' }));
    return [protectedHeader, encoded(wrapped), encoded(iv), encoded(ciphertext.subarray(0, -16)), encoded(ciphertext.subarray(-16))].join('.');
  } finally { plaintext.fill(0); }
}

export async function submitSecret(input: HTMLInputElement, target: SecretTarget, signal: AbortSignal, client: APIClient = api) {
  // Read directly into a short-lived byte buffer; never place the key in component/query/store state.
  const plaintext = new TextEncoder().encode(input.value);
  input.value = '';
  try {
    if (plaintext.byteLength < 1 || plaintext.byteLength > 8192) throw new AppError('INVALID_INPUT');
    if (!globalThis.crypto?.subtle) throw new AppError('SECRET_ENCRYPTION_UNAVAILABLE');
    const challenge = await client.secretChallenge(target, signal);
    if (signal.aborted) throw new AppError('CANCELLED');
    const envelope = await encryptSecret(plaintext, challenge, target);
    if (signal.aborted) throw new AppError('CANCELLED');
    const result = await client.submitSecret({ challengeId: challenge.challengeId, envelope }, signal);
    if (result.resourceId !== target.resourceId || result.credentialRevision <= target.expectedRevision) throw new AppError('SECRET_SUBMISSION_INVALID');
    return result;
  } catch (error) {
    if (error instanceof AppError) throw error;
    throw new AppError('SECRET_SUBMISSION_INVALID');
  } finally { plaintext.fill(0); }
}
