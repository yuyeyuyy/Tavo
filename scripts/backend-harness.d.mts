export interface BackendHarness {
  directory: string; origin: string; tokenFile: string;
  request<T = unknown>(path: string, options?: RequestInit): Promise<T>;
  start(): Promise<void>; stop(): Promise<void>; cleanup(): Promise<void>;
  session(): string; docsCredentials(): { username: string; password: string };
}
export function startBackend(options?: { directory?: string; source?: string; origins?: string[] }): Promise<BackendHarness>;
export function pause(ms: number): Promise<void>;
export function freePort(): Promise<number>;
