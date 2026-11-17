import { afterEach, describe, expect, it, vi } from 'vitest';
import { createChatStore } from '../apps/desktop/src/renderer/chat/store';
import { mockTransport } from '../apps/desktop/src/renderer/chat/mock';
import { MESSAGE_LIMIT, REPLY_ERROR, type ChatInput } from '../apps/desktop/src/renderer/chat/types';

function deferred() { let resolve!: (value: string) => void; let reject!: (error: Error) => void; const promise = new Promise<string>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
afterEach(() => vi.useRealTimers());
describe('conversation lifecycle', () => {
  it('rejects blank/oversized input and concurrent sends, retaining completed context', async () => {
    const pending = deferred(); const reply = vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValue('second');
    const store = createChatStore({ reply });
    expect(store.getState().send('  ')).toBe(false); expect(store.getState().send('x'.repeat(MESSAGE_LIMIT + 1))).toBe(false);
    expect(store.getState().send(' hello ')).toBe(true); expect(store.getState().send('overlap')).toBe(false);
    expect(store.getState().messages.map((m) => m.status)).toEqual(['complete', 'pending']);
    pending.resolve(' first '); await Promise.resolve();
    expect(store.getState().activeRequestId).toBeNull();
    expect(store.getState().send('next')).toBe(true); await Promise.resolve();
    expect(reply.mock.calls[1]![0]).toEqual([{ role: 'user', content: 'hello' }, { role: 'assistant', content: 'first' }, { role: 'user', content: 'next' }]);
  });
  it('ignores a late response after stop even when retried before it completes', async () => {
    const old = deferred(), current = deferred(); const signals: AbortSignal[] = [];
    const reply = vi.fn((_messages: ChatInput[], signal: AbortSignal) => { signals.push(signal); return signals.length === 1 ? old.promise : current.promise; });
    const store = createChatStore({ reply }); store.getState().send('hello'); store.getState().stop();
    expect(signals[0]!.aborted).toBe(true); expect(store.getState().messages.at(-1)?.status).toBe('cancelled');
    store.getState().retry(); const id = store.getState().activeRequestId;
    old.resolve('late'); await Promise.resolve(); expect(store.getState().activeRequestId).toBe(id);
    current.resolve('current'); await Promise.resolve();
    expect(store.getState().messages.map((m) => m.content)).toEqual(['hello', 'current']);
  });
  it('does not resurrect an old conversation or cancel a newer request', async () => {
    const old = deferred(), current = deferred(); const reply = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    const store = createChatStore({ reply }); store.getState().send('old'); store.getState().newConversation();
    expect(store.getState().messages).toEqual([]); store.getState().send('new');
    old.reject(new Error('private-error')); await Promise.resolve(); expect(store.getState().messages[0]?.content).toBe('new');
    current.resolve('new reply'); await Promise.resolve(); expect(store.getState().messages).toHaveLength(2);
  });
  it('sanitizes failures and retries without duplicating the user turn', async () => {
    const reply = vi.fn().mockRejectedValueOnce(new Error('synthetic-private-detail')).mockResolvedValue('recovered');
    const store = createChatStore({ reply }); store.getState().send('hello'); await Promise.resolve();
    expect(store.getState().messages.at(-1)).toMatchObject({ status: 'failed', content: REPLY_ERROR });
    store.getState().retry(); await Promise.resolve();
    expect(store.getState().messages.map((m) => m.content)).toEqual(['hello', 'recovered']);
    expect(reply.mock.calls[1]![0]).toEqual([{ role: 'user', content: 'hello' }]);
  });
  it.each(['', 'x'.repeat(16001)])('rejects an invalid adapter response %#', async (value) => {
    const store = createChatStore({ reply: async () => value }); store.getState().send('hello'); await Promise.resolve();
    expect(store.getState().messages.at(-1)).toMatchObject({ status: 'failed', content: REPLY_ERROR });
  });
});
describe('local Mock adapter', () => {
  it('returns an explicitly bounded demo without running real tools', async () => {
    vi.useFakeTimers(); const result = mockTransport.reply([{ role: 'user', content: '工具如何工作' }], new AbortController().signal);
    await vi.advanceTimersByTimeAsync(900); expect(await result).toContain('离线 Mock 演示');
    expect(await result).not.toMatch(/只读工具|calculate|get_current_time|workspace_write/);
  });
  it('cleans up a cancelled delay', async () => {
    vi.useFakeTimers(); const controller = new AbortController();
    const result = mockTransport.reply([{ role: 'user', content: 'hello' }], controller.signal); const rejected = expect(result).rejects.toThrow('CANCELLED');
    controller.abort(); await rejected; expect(vi.getTimerCount()).toBe(0);
  });
});
