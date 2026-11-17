import { beforeAll, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { AppError } from '@tavo/shared';
import { APIClient } from '../apps/desktop/src/renderer/api/client';
import { challengeSchema, type SecretChallenge } from '../apps/desktop/src/renderer/security/contracts';
import { encryptSecret, submitSecret } from '../apps/desktop/src/renderer/security/secret-submission';

const KEY = 'synthetic-browser-input-key-not-real';
let challenge: SecretChallenge;
let privateKey: ArrayBuffer;
const target = { purpose: 'model.api_key' as const, resourceId: 'test', expectedRevision: 0 };
beforeAll(async () => {
  const pair = await crypto.subtle.generateKey({ name: 'RSA-OAEP', modulusLength: 3072, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' }, true, ['wrapKey', 'unwrapKey']);
  const jwk = await crypto.subtle.exportKey('jwk', pair.publicKey);
  privateKey = await crypto.subtle.exportKey('pkcs8', pair.privateKey);
  challenge = { version: 1, challengeId: 'c'.repeat(32), expiresAt: new Date(Date.now() + 120000).toISOString(), kid: 'k'.repeat(32), purpose: 'model.api_key', resourceId: 'test', credentialRevision: 0, publicJwk: { kty: 'RSA', n: jwk.n!, e: 'AQAB', alg: 'RSA-OAEP-256', ext: true, keyOps: ['wrapKey'] } };
});

function backendDecrypt(envelope: string, expected = KEY) {
  const data = { envelope, expected, private: Buffer.from(privateKey).toString('base64'), header: { v: 1, typ: 'tavo-secret+jwe', alg: 'RSA-OAEP-256', enc: 'A256GCM', kid: challenge.kid, challengeId: challenge.challengeId, purpose: challenge.purpose, resourceId: challenge.resourceId } };
  return execFileSync(resolve('backend/.venv/bin/python'), ['-c', 'import sys,json,base64; from cryptography.hazmat.primitives.serialization import load_der_private_key; from app.credentials.envelope import decrypt; from app.credentials.contracts import ProtectedHeader; data=json.load(sys.stdin); key=load_der_private_key(base64.b64decode(data["private"]),None); result=decrypt(key,data["envelope"],ProtectedHeader.model_validate(data["header"])); assert result.get_secret_value()==data["expected"]; print("ok")'], { cwd: resolve('backend'), input: JSON.stringify(data), encoding: 'utf8' }).trim();
}

describe('Web Crypto to Python credential interoperability', () => {
  it.each([KEY, 'x'.repeat(8192)])('encrypts the complete bounded credential with random fresh keys %#', async (secret) => {
    const bytes = new TextEncoder().encode(secret);
    const envelope = await encryptSecret(bytes, challenge, target);
    expect(bytes.every((n) => n === 0)).toBe(true); expect(envelope.split('.')).toHaveLength(5);
    expect(envelope).not.toContain(secret); expect(backendDecrypt(envelope, secret)).toBe('ok');
    const second = await encryptSecret(new TextEncoder().encode(secret), challenge, target);
    expect(second).not.toBe(envelope); expect(backendDecrypt(second, secret)).toBe('ok');
  });
  it('rejects scope mismatch and removes the plaintext buffer on failure', async () => {
    const bytes = new TextEncoder().encode(KEY);
    await expect(encryptSecret(bytes, challenge, { ...target, resourceId: 'other' })).rejects.toMatchObject({ code: 'SECRET_CHALLENGE_INVALID' });
    expect(bytes.every((n) => n === 0)).toBe(true);
    expect(challengeSchema.safeParse({ ...challenge, publicJwk: { ...challenge.publicJwk, d: 'private' } }).success).toBe(false);
  });
  it('does not downgrade when crypto is unavailable', async () => {
    const bytes = new TextEncoder().encode(KEY);
    await expect(encryptSecret(bytes, challenge, target, {} as Crypto)).rejects.toMatchObject({ code: 'SECRET_ENCRYPTION_UNAVAILABLE' });
    expect(bytes.every((n) => n === 0)).toBe(true);
  });
});

describe('credential input and HTTP boundary', () => {
  const native = () => ({ profile: async () => ({ backendUrl: 'http://127.0.0.1:8000', authenticated: true, startMinimized: false }) }) as NativeDesktopAPI;
  it('clears the DOM before requests, sends only an envelope and returns no saved key', async () => {
    const input = { value: KEY } as HTMLInputElement;
    const transport = vi.fn<typeof fetch>().mockImplementation(async (_url, options) => {
      expect(input.value).toBe(''); expect(String(options?.body)).not.toContain(KEY);
      return new Response(JSON.stringify(transport.mock.calls.length === 1 ? challenge : { resourceId: 'test', credentialRevision: 1, ready: true }));
    });
    const result = await submitSecret(input, target, new AbortController().signal, new APIClient(native, transport));
    expect(result).toEqual({ resourceId: 'test', credentialRevision: 1, ready: true });
    const submitted = JSON.parse(String(transport.mock.calls[1]![1]?.body));
    expect(Object.keys(submitted).sort()).toEqual(['challengeId', 'envelope']);
    expect(backendDecrypt(submitted.envelope)).toBe('ok');
    expect(transport.mock.calls[1]![1]?.headers).not.toHaveProperty('Authorization');
  });
  it('clears input on failure and never retries or submits plaintext', async () => {
    const input = { value: KEY } as HTMLInputElement;
    const transport = vi.fn<typeof fetch>().mockRejectedValue(new Error('synthetic-private-detail'));
    await expect(submitSecret(input, target, new AbortController().signal, new APIClient(native, transport))).rejects.toBeInstanceOf(AppError);
    expect(input.value).toBe(''); expect(transport).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(transport.mock.calls)).not.toContain(KEY);
  });
  it('aborts after the challenge without sending the encrypted submission', async () => {
    const input = { value: KEY } as HTMLInputElement; const controller = new AbortController();
    const transport = vi.fn<typeof fetch>().mockImplementation(async () => { controller.abort(); return new Response(JSON.stringify(challenge)); });
    await expect(submitSecret(input, target, controller.signal, new APIClient(native, transport))).rejects.toMatchObject({ code: 'CANCELLED' });
    expect(input.value).toBe(''); expect(transport).toHaveBeenCalledTimes(1);
  });
});
