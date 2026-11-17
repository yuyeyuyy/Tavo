import { api, APIClient } from '../api/client';
import { AppError } from '@tavo/shared';
import type { ChatTransport } from './types';
export function httpChatTransport(profileId: string, client: APIClient = api): ChatTransport {
  return { async reply(messages, signal) {
    const value = { profileId, messages: [...messages] };
    if (new TextEncoder().encode(JSON.stringify(value)).length > 24576) throw new AppError('CONTEXT_TOO_LARGE');
    const result = await client.chat(value, signal);
    if (result.profileId !== profileId) throw new AppError('MODEL_RESPONSE_INVALID');
    return { content: result.message.content, truncated: result.truncated };
  } };
}
