import { createStore } from 'zustand/vanilla';
import { z } from 'zod';
import { AppError, publicError } from '@tavo/shared';
import { CONTEXT_ERROR, MESSAGE_LIMIT, REPLY_ERROR, type ChatInput, type ChatMessage, type ChatTransport } from './types';
interface ChatState {
  messages: ChatMessage[];
  activeRequestId: string | null;
  error: string | null;
  send(content: string): boolean;
  stop(): void;
  retry(): void;
  newConversation(): void;
}
interface Runtime { id(): string; now(): string }
const replySchema = z.string().trim().min(1).max(16000);
// Cancellation and identity checks have separate jobs: late responses may ignore abort.
export function createChatStore(transport: ChatTransport, runtime: Runtime = { id: () => crypto.randomUUID(), now: () => new Date().toISOString() }) {
  let controller: AbortController | undefined;
  return createStore<ChatState>()((set, get) => {
    const message = (role: ChatMessage['role'], content: string, status: ChatMessage['status']): ChatMessage => ({ id: runtime.id(), role, content, createdAt: runtime.now(), status });
    async function respond(id: string, inputs: ChatInput[]) {
      const pending = new AbortController(); controller = pending;
      try {
        const result = await transport.reply(inputs, pending.signal);
        const content = replySchema.parse(typeof result === 'string' ? result : result.content);
        const truncated = typeof result === 'string' ? false : result.truncated;
        if (pending.signal.aborted || get().activeRequestId !== id) return;
        set((state) => ({ activeRequestId: null, messages: state.messages.map((row) => row.id === id ? { ...row, content, truncated, status: 'complete' } : row) }));
      } catch (error) {
        if (pending.signal.aborted || get().activeRequestId !== id) return;
        const content = error instanceof AppError ? publicError(error).message : REPLY_ERROR;
        set((state) => ({ activeRequestId: null, messages: state.messages.map((row) => row.id === id ? { ...row, content, status: 'failed' } : row) }));
      } finally { if (controller === pending) controller = undefined; }
    }
    function start(messages: ChatMessage[]) {
      const inputs: ChatInput[] = [];
      for (let index = 0; index < messages.length; index++) {
        const row = messages[index]!; const next = messages[index + 1];
        if (row.role === 'user' && (index === messages.length - 1 || next?.status === 'complete')) inputs.push({ role: row.role, content: row.content });
        if (row.role === 'assistant' && row.status === 'complete') inputs.push({ role: row.role, content: row.content });
      }
      if (inputs.length > 20 || new TextEncoder().encode(JSON.stringify(inputs)).length > 24300) { set({ error: CONTEXT_ERROR }); return false; }
      const pending = message('assistant', '', 'pending');
      set({ messages: [...messages, pending], activeRequestId: pending.id, error: null }); void respond(pending.id, inputs); return true;
    }
    return {
      messages: [], activeRequestId: null, error: null,
      send(content) {
        const trimmed = content.trim();
        if (get().activeRequestId || !trimmed || trimmed.length > MESSAGE_LIMIT) return false;
        return start([...get().messages, message('user', trimmed, 'complete')]);
      },
      stop() {
        const id = get().activeRequestId; if (!id) return;
        const previous = controller; controller = undefined;
        set((state) => ({ activeRequestId: null, messages: state.messages.map((row) => row.id === id ? { ...row, content: '', status: 'cancelled' } : row) })); previous?.abort();
      },
      retry() {
        const { messages, activeRequestId } = get(); const last = messages.at(-1);
        if (activeRequestId || !last || last.role !== 'assistant' || !['failed', 'cancelled'].includes(last.status)) return;
        start(messages.slice(0, -1));
      },
      newConversation() { const previous = controller; controller = undefined; set({ messages: [], activeRequestId: null, error: null }); previous?.abort(); },
    };
  });
}
