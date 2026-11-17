import { z } from 'zod';
import { AppError, errorCodeSchema, type ErrorCode } from '@tavo/shared';
import type { components } from './generated';
import { modelCatalogSchema, chatReplySchema } from './chat';
import { challengeSchema, submissionAckSchema } from '../security/contracts';
import { modelProfileCreateSchema, modelProfileSchema, providerListSchema, type ModelProfileCreate } from './model-profiles';
import { approvalPreviewSchema, networkTargetsSchema, agentRuntimeSchema, sessionSchema, sessionPageSchema, messagePageSchema, acceptedSchema, runSchema, decisionSchema, messageWriteSchema, sessionCreateSchema, decisionWriteSchema, type SessionCreate, type MessageWrite, type DecisionWrite } from './sessions';

type Schemas = components['schemas'];
const profileSchema = z.strictObject({ backendUrl: z.string().url(), authenticated: z.boolean(), startMinimized: z.boolean() });
export class APIError extends AppError {}
export class APIClient {
  constructor(private readonly native: () => NativeDesktopAPI = () => window.desktop, private readonly transport: typeof fetch = (...args) => fetch(...args)) {}
  async profile() { return profileSchema.parse(await this.native().profile()); }
  async request<T>(path: string, options: RequestInit = {}, timeoutMs = 5000, timeoutCode: ErrorCode = 'TIMEOUT'): Promise<T> {
    const profile = await this.profile(); const timeout = AbortSignal.timeout(timeoutMs);
    try {
      const response = await this.transport(new URL(`/api/v1${path}`, profile.backendUrl), { ...options, credentials: 'omit', redirect: 'error', signal: options.signal ? AbortSignal.any([options.signal, timeout]) : timeout, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
      if (!response.ok) {
        const body = await response.json().catch(() => null); const parsed = errorCodeSchema.safeParse(body?.error?.code);
        throw new APIError(parsed.success ? parsed.data : response.status === 401 ? 'UNAUTHENTICATED' : 'INTERNAL');
      }
      return (response.status === 204 ? undefined : await response.json()) as T;
    } catch (error) {
      if (error instanceof AppError) throw error;
      if (options.signal?.aborted) throw new APIError('CANCELLED');
      throw new APIError(timeout.aborted ? timeoutCode : 'BACKEND_UNAVAILABLE');
    }
  }
  async models() { return modelCatalogSchema.parse(await this.request<Schemas['ModelCatalog']>('/chat/models')); }
  async systemStatus() { return z.strictObject({security:z.strictObject({available:z.boolean(),backend:z.string(),dataLocation:z.string()}),workerActive:z.boolean(),mode:z.literal('single-owner')}).parse(await this.request<Schemas['SystemStatus']>('/system/status')); }
  async modelProviders(signal?: AbortSignal) { return providerListSchema.parse(await this.request<Schemas['ProviderList']>('/model-providers', { signal })); }
  async addModelProfile(value: ModelProfileCreate, signal?: AbortSignal) {
    const input = modelProfileCreateSchema.safeParse(value);
    if (!input.success) throw new APIError('INVALID_INPUT');
    return modelProfileSchema.parse(await this.request<Schemas['ModelProfileOut']>('/model-profiles', { method: 'POST', body: JSON.stringify(input.data), signal }));
  }
  async secretChallenge(value: Schemas['ChallengeRequest'], signal?: AbortSignal) {
    const parsed = challengeSchema.safeParse(await this.request<Schemas['ChallengeOut']>('/secret-submissions/challenges', { method: 'POST', body: JSON.stringify(value), signal }, 10000));
    if (!parsed.success) throw new APIError('SECRET_SUBMISSION_INVALID');
    return parsed.data;
  }
  async submitSecret(value: Schemas['SecretSubmission'], signal?: AbortSignal) {
    const parsed = submissionAckSchema.safeParse(await this.request<Schemas['SubmissionAck']>('/secret-submissions', { method: 'POST', body: JSON.stringify(value), signal }, 10000));
    if (!parsed.success) throw new APIError('SECRET_SUBMISSION_INVALID');
    return parsed.data;
  }
  async chat(value: Schemas['ChatRequest'], signal?: AbortSignal) {
    const result = chatReplySchema.safeParse(await this.request<Schemas['ChatReply']>('/chat/completions', { method: 'POST', body: JSON.stringify(value), signal }, 65000, 'MODEL_TIMEOUT'));
    if (!result.success) throw new APIError('MODEL_RESPONSE_INVALID');
    return result.data;
  }

  async approvalPreview(sessionId: string, callId: string) { return approvalPreviewSchema.parse(await this.request<Schemas['ApprovalPreview']>('/sessions/' + encodeURIComponent(sessionId) + '/tool-calls/' + encodeURIComponent(callId) + '/approval')); }
  async networkTargets() { return networkTargetsSchema.parse(await this.request<Schemas['NetworkTargetList']>('/sandbox-network-targets')); }
  async agentRuntime() { return agentRuntimeSchema.parse(await this.request<Schemas['AgentRuntime']>('/agent-runtime')); }
  async listSessions(before?: string, signal?: AbortSignal) { return sessionPageSchema.parse(await this.request<Schemas['SessionPage']>('/sessions' + (before ? '?before=' + encodeURIComponent(before) : ''), { signal })); }
  async createSession(value: SessionCreate, signal?: AbortSignal) { return sessionSchema.parse(await this.request<Schemas['Session']>('/sessions', { method: 'POST', body: JSON.stringify(sessionCreateSchema.parse(value)), signal })); }
  async getSession(id: string, signal?: AbortSignal) { return sessionSchema.parse(await this.request<Schemas['Session']>('/sessions/' + encodeURIComponent(id), { signal })); }
  async sessionMessages(id: string, options: { beforeSequence?: number; afterSequence?: number; limit?: number } = {}, signal?: AbortSignal) { const query=new URLSearchParams(); for (const [key,value] of Object.entries(options)) if(value!==undefined) query.set(key,String(value)); return messagePageSchema.parse(await this.request<Schemas['MessagePage']>('/sessions/' + encodeURIComponent(id) + '/messages?' + query, { signal })); }
  async sendMessage(id: string, value: MessageWrite, signal?: AbortSignal) { return acceptedSchema.parse(await this.request<Schemas['MessageAccepted']>('/sessions/' + encodeURIComponent(id) + '/messages', { method: 'POST', body: JSON.stringify(messageWriteSchema.parse(value)), signal })); }
  async sessionRun(id: string, runId: string, signal?: AbortSignal) { return runSchema.parse(await this.request<Schemas['Run']>('/sessions/' + encodeURIComponent(id) + '/runs/' + encodeURIComponent(runId), { signal })); }
  async cancelRun(id: string, runId: string) { return runSchema.parse(await this.request<Schemas['Run']>('/sessions/' + encodeURIComponent(id) + '/runs/' + encodeURIComponent(runId) + '/cancel', { method: 'POST' })); }
  async retryRun(id: string, runId: string) { return runSchema.parse(await this.request<Schemas['Run']>('/sessions/' + encodeURIComponent(id) + '/runs/' + encodeURIComponent(runId) + '/retry', { method: 'POST' })); }
  async decide(id: string, callId: string, value: DecisionWrite) { return decisionSchema.parse(await this.request<Schemas['Decision']>('/sessions/' + encodeURIComponent(id) + '/tool-calls/' + encodeURIComponent(callId) + '/decision', { method: 'POST', body: JSON.stringify(decisionWriteSchema.parse(value)) })); }
}
export const api = new APIClient();
