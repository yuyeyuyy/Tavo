export const MESSAGE_LIMIT = 4000;
export const REPLY_ERROR = '暂时无法生成回复，请重试。';
export const CONTEXT_ERROR = '对话超过 20 条消息或 24 KiB 输入限制，请开始新对话后继续。';
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: string;
  status: 'pending' | 'complete' | 'failed' | 'cancelled';
  truncated?: boolean;
}
export interface ChatInput { role: ChatMessage['role']; content: string }
export interface ChatOutput { content: string; truncated: boolean }
export interface ChatTransport { reply(messages: readonly ChatInput[], signal: AbortSignal): Promise<string | ChatOutput> }
