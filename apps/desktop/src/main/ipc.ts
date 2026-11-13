import { AppError, publicError, requestSchemas, responseSchema, type Operation, type Response } from '@tavo/shared';
import type { ApplicationRuntime } from '@tavo/core';
import type { WorkspaceRuntime } from '@tavo/core';
import { workspaceRequestSchemas, type WorkspaceOperation } from '@tavo/shared';

export interface SenderIdentity { url: string; isMainFrame: boolean; isOwner: boolean }
export function isTrustedSender(sender: SenderIdentity, trustedURL: string): boolean {
  if (!sender.isMainFrame || !sender.isOwner) return false;
  try {
    const url = new URL(sender.url);
    const trusted = new URL(trustedURL);
    // 精确 URL 匹配，不接受路径前缀、同源其他页面、子框架。
    url.hash = ''; trusted.hash = '';
    return url.href === trusted.href;
  } catch { return false; }
}

export async function dispatchWorkspaceRequest(
  runtime: Pick<WorkspaceRuntime, 'snapshot' | 'analyze' | 'execute'>,
  operation: WorkspaceOperation | 'snapshot' | 'analyze', input: unknown, sender: SenderIdentity, trustedURL: string,
): Promise<Response> {
  try {
    if (!isTrustedSender(sender, trustedURL)) throw new AppError('FORBIDDEN');
    let data;
    if (operation === 'snapshot') {
      if (!requestSchemas.snapshot.safeParse(input).success) throw new AppError('INVALID_INPUT'); data = runtime.snapshot();
    } else if (operation === 'analyze') {
      const value = requestSchemas.analyze.safeParse(input); if (!value.success) throw new AppError('INVALID_INPUT');
      data = await runtime.analyze(value.data.eventId);
    } else {
      if (!workspaceRequestSchemas[operation].safeParse(input).success) throw new AppError('INVALID_INPUT');
      data = await runtime.execute(operation, input);
    }
    return responseSchema.parse({ ok: true, data });
  } catch (error) { return { ok: false, error: publicError(error) }; }
}
export async function dispatchRequest(
  runtime: Pick<ApplicationRuntime, 'snapshot' | 'sync' | 'analyze'>,
  operation: Operation, input: unknown, sender: SenderIdentity, trustedURL: string,
): Promise<Response> {
  try {
    if (!isTrustedSender(sender, trustedURL)) throw new AppError('FORBIDDEN');
    let data;
    switch (operation) {
      case 'snapshot': {
        if (!requestSchemas.snapshot.safeParse(input).success) throw new AppError('INVALID_INPUT');
        data = runtime.snapshot(); break;
      }
      case 'sync': {
        const parsed = requestSchemas.sync.safeParse(input);
        if (!parsed.success) throw new AppError('INVALID_INPUT');
        data = await runtime.sync(parsed.data.connectorId); break;
      }
      case 'analyze': {
        const parsed = requestSchemas.analyze.safeParse(input);
        if (!parsed.success) throw new AppError('INVALID_INPUT');
        data = await runtime.analyze(parsed.data.eventId); break;
      }
    }
    return responseSchema.parse({ ok: true, data });
  } catch (error) { return { ok: false, error: publicError(error) }; }
}
