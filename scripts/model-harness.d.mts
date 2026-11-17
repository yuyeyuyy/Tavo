import type { BackendHarness } from './backend-harness.mjs';
export function startModelBackend(options?: { withKey?: boolean; origins?: string[]; empty?: boolean }): Promise<{ backend: BackendHarness; calls: { messages: { role: string; content: string }[] }[]; failNext(count?: number): void; setDelay(value: number): void; cleanup(): Promise<void> }>;
