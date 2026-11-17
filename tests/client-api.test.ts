import { afterEach, describe, expect, it, vi } from 'vitest';
import { APIClient } from '../apps/desktop/src/renderer/api/client';
import { BackendConnection, backendOrigin } from '../apps/desktop/src/main/backend';
import { mkdtemp, writeFile, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const profile = { backendUrl: 'http://127.0.0.1:8000', authenticated: true, startMinimized: false };
const native = () => ({ profile: async () => profile }) as NativeDesktopAPI;
afterEach(() => vi.unstubAllGlobals());

describe('independent API client', () => {
  it('uses direct REST with no credential in the Renderer request', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ status: 'ok' })));
    const api = new APIClient(native, transport);
    expect(await api.request('/health')).toEqual({ status: 'ok' });
    const [url, options] = transport.mock.calls[0]!;
    expect(String(url)).toBe('http://127.0.0.1:8000/api/v1/health');
    expect(options).toMatchObject({ credentials: 'omit', redirect: 'error' });
    expect(options?.headers).not.toHaveProperty('Authorization');
  });
  it('normalizes server and transport errors without exposing response text', async () => {
    const api = new APIClient(native, vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'UNAUTHENTICATED', message: 'sensitive-value' } }), { status: 401 })));
    await expect(api.request('/health')).rejects.toMatchObject({ code: 'UNAUTHENTICATED' });
    const offline = new APIClient(native, vi.fn<typeof fetch>().mockRejectedValue(new Error('private-path')));
    await expect(offline.request('/health')).rejects.toMatchObject({ code: 'BACKEND_UNAVAILABLE' });
  });
});

describe('native backend configuration', () => {
  it('requires HTTPS for remote URLs and disallows URL credentials', () => {
    expect(backendOrigin('http://127.0.0.1:8000/')).toBe(profile.backendUrl);
    expect(backendOrigin('https://example.com')).toBe('https://example.com');
    for (const value of ['http://example.com', 'https://user:secret@example.com', 'https://example.com/path', 'https://example.com?token=secret']) expect(() => backendOrigin(value)).toThrow();
  });
  it('keeps owner/session material out of profiles and rolls back failed configuration', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'tavo-native-auth-'));
    try {
      const tokenFile = join(directory, 'owner'); const token = 'synthetic-owner-'.repeat(4);
      await writeFile(tokenFile, token, { mode: 0o600 });
      const transport = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ accessToken: 'synthetic-session-'.repeat(4), expiresAt: new Date(Date.now() + 900_000).toISOString() }))).mockResolvedValueOnce(new Response('', { status: 401 }));
      vi.stubGlobal('fetch', transport);
      const backend = new BackendConnection(join(directory, 'profile.json'), profile.backendUrl, tokenFile);
      await backend.authenticate(); expect(JSON.stringify(backend.profile())).not.toContain('synthetic');
      await expect(backend.configure('https://example.com', tokenFile)).rejects.toThrow();
      expect(backend.profile().backendUrl).toBe(profile.backendUrl);
      await symlink(tokenFile, join(directory, 'link'));
      const unsafe = new BackendConnection(join(directory, 'other.json'), profile.backendUrl, join(directory, 'link'));
      await expect(unsafe.authenticate()).rejects.toThrow(); expect(transport).toHaveBeenCalledTimes(2);
    } finally { await rm(directory, { recursive: true, force: true }); }
  });
});
