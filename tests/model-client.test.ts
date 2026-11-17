import { describe, expect, it, vi } from 'vitest';
import { APIClient } from '../apps/desktop/src/renderer/api/client';
import { chatReplySchema, modelCatalogSchema } from '../apps/desktop/src/renderer/api/chat';
import { createChatStore } from '../apps/desktop/src/renderer/chat/store';
import { httpChatTransport } from '../apps/desktop/src/renderer/chat/http';
import { AppError, publicError } from '@tavo/shared';

const profile = { backendUrl: 'http://127.0.0.1:8000', authenticated: true, startMinimized: false };
const result = { profileId: 'test', provider: 'deepseek', model: 'text', message: { role: 'assistant', content: 'answer' }, finishReason: 'stop', truncated: false, usage: null };
describe('model HTTP boundary', () => {
  it.each(['MODEL_OUTPUT_LIMIT', 'MODEL_EMPTY_RESPONSE'] as const)('recognizes %s from the backend without showing refusal', async code => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify({ error: { code } }), { status: 502, headers: { 'Content-Type': 'application/json' } }));
    const client = new APIClient(() => ({ profile: async () => profile }) as NativeDesktopAPI, fetch);
    await expect(httpChatTransport('test', client).reply([{ role: 'user', content: 'hello' }], new AbortController().signal)).rejects.toMatchObject({ code });
    expect(publicError(new AppError(code)).message).toMatch(code === 'MODEL_OUTPUT_LIMIT' ? /生成上限/ : /空回复/);
  });
  it('sends only a whitelisted profile and text messages with a cancellation signal', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(result)));
    const client = new APIClient(() => ({ profile: async () => profile }) as NativeDesktopAPI, fetch);
    const controller = new AbortController();
    expect(await httpChatTransport('test', client).reply([{ role: 'user', content: 'hello' }], controller.signal)).toEqual({ content: 'answer', truncated: false });
    const [url, options] = fetch.mock.calls[0]!;
    expect(String(url)).toBe('http://127.0.0.1:8000/api/v1/chat/completions');
    expect(JSON.parse(String(options?.body))).toEqual({ profileId: 'test', messages: [{ role: 'user', content: 'hello' }] });
    expect(options?.headers).not.toHaveProperty('Authorization');
    controller.abort(); expect(options?.signal?.aborted).toBe(true);
  });
  it('rejects credentials or unknown response fields and mismatched profiles', async () => {
    expect(modelCatalogSchema.safeParse({ items: [{ id: 'test', label: 'test', provider: 'deepseek', model: 'text', ready: true, apiKey: 'synthetic' }], defaultProfileId: null }).success).toBe(false);
    expect(chatReplySchema.safeParse({ ...result, secretRef: 'synthetic' }).success).toBe(false);
    const client = new APIClient(() => ({ profile: async () => profile }) as NativeDesktopAPI, vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(result))));
    await expect(httpChatTransport('other', client).reply([{ role: 'user', content: 'hello' }], new AbortController().signal)).rejects.toMatchObject({ code: 'MODEL_RESPONSE_INVALID' });
  });
  it('starts a new user turn after cancellation without resending an unanswered old user message', async () => {
    const reply = vi.fn().mockImplementationOnce(() => new Promise(() => {})).mockResolvedValue({ content: 'bounded', truncated: true });
    const store = createChatStore({ reply }); store.getState().send('old'); store.getState().stop(); store.getState().send('new'); await Promise.resolve();
    expect(reply.mock.calls[1]![0]).toEqual([{ role: 'user', content: 'new' }]);
    expect(store.getState().messages.at(-1)?.truncated).toBe(true);
  });
  it('requires a new conversation before exceeding the bounded history', async () => {
    const store = createChatStore({ reply: async () => 'answer' });
    for (let i = 0; i < 10; i++) { expect(store.getState().send('turn')).toBe(true); await Promise.resolve(); }
    expect(store.getState().send('over limit')).toBe(false); expect(store.getState().error).toContain('开始新对话'); expect(store.getState().messages).toHaveLength(20);
  });
});
