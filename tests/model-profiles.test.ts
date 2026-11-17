import { describe, expect, it, vi } from 'vitest';
import { APIClient } from '../apps/desktop/src/renderer/api/client';

const native = () => ({ profile: async () => ({ backendUrl: 'http://127.0.0.1:8000', authenticated: true, startMinimized: false }) }) as NativeDesktopAPI;
const profile = { id: 'model-123', label: '学习', provider: 'deepseek', model: 'fixture', ready: false, credentialRevision: 0, enabled: true };
describe('client model configuration boundary', () => {
  it('creates a profile using public fields only, without sending a key or authorization material', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(profile), { status: 201 }));
    const client = new APIClient(native, fetch);
    expect(await client.addModelProfile({ provider: 'deepseek', model: 'fixture', label: '学习' })).toEqual(profile);
    const [url, options] = fetch.mock.calls[0]!;
    expect(String(url)).toBe('http://127.0.0.1:8000/api/v1/model-profiles');
    expect(JSON.parse(String(options?.body))).toEqual({ provider: 'deepseek', model: 'fixture', label: '学习' });
    expect(options?.headers).not.toHaveProperty('Authorization');
  });
  it('rejects credential and destination fields before sending an ordinary JSON request', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(); const client = new APIClient(native, fetch);
    await expect(client.addModelProfile({ provider: 'deepseek', model: 'fixture', apiKey: 'synthetic' } as never)).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(client.addModelProfile({ provider: 'deepseek', model: 'fixture', baseUrl: 'https://other.invalid' } as never)).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(client.addModelProfile({ provider: 'deepseek', model: 'SET_MODEL_ID' })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    expect(fetch).not.toHaveBeenCalled();
  });
  it('accepts public provider metadata and rejects secret-bearing responses without retrying creation', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: 'deepseek', label: 'DeepSeek', modelHint: '填写模型 ID' }] }))).mockResolvedValueOnce(new Response(JSON.stringify({ ...profile, apiKey: 'synthetic' })));
    const client = new APIClient(native, fetch);
    expect((await client.modelProviders()).items[0]?.id).toBe('deepseek');
    await expect(client.addModelProfile({ provider: 'deepseek', model: 'fixture' })).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
