import { describe, expect, it, vi } from 'vitest';
import { dispatchRequest, isTrustedSender } from '../apps/desktop/src/main/ipc';
import { requestSchemas, snapshotSchema } from '@tavo/shared';

const trusted = 'file:///app/renderer/index.html';
const sender = { url: trusted, isMainFrame: true, isOwner: true };
function fakeRuntime() {
  const value = snapshotSchema.parse({ connectors: [], accounts: [], tasks: [], events: [] });
  return { snapshot: vi.fn(() => value), sync: vi.fn(async () => value), analyze: vi.fn(async () => value) };
}
describe('安全 IPC', () => {
  it.each([null, undefined, [], { connectorId: 'other' }, { connectorId: 'mock', secret: 'synthetic' }, { connectorId: { toString: 'mock' } }])('拒绝非法同步参数 %#', async (input) => {
    const runtime = fakeRuntime();
    expect(await dispatchRequest(runtime, 'sync', input, sender, trusted)).toMatchObject({ ok: false, error: { code: 'INVALID_INPUT' } });
    expect(runtime.sync).not.toHaveBeenCalled();
  });
  it.each([
    { ...sender, url: 'https://untrusted.example' }, { ...sender, isMainFrame: false },
    { ...sender, isOwner: false }, { ...sender, url: trusted + '?other=1' },
    { ...sender, url: trusted + '/other' },
  ])('拒绝不可信来源 %#', async (identity) => {
    const runtime = fakeRuntime();
    expect(await dispatchRequest(runtime, 'snapshot', {}, identity, trusted)).toMatchObject({ ok: false, error: { code: 'FORBIDDEN' } });
    expect(runtime.snapshot).not.toHaveBeenCalled();
  });
  it('精确匹配开发地址，拒绝其他端口或页面', () => {
    expect(isTrustedSender({ ...sender, url: 'http://localhost:5173/' }, 'http://localhost:5173')).toBe(true);
    expect(isTrustedSender({ ...sender, url: 'http://localhost:5174/' }, 'http://localhost:5173')).toBe(false);
    expect(isTrustedSender({ ...sender, url: 'not-a-url' }, trusted)).toBe(false);
  });
  it('合法请求返回校验后的数据，异常不会跨 IPC 暴露', async () => {
    const runtime = fakeRuntime();
    expect(await dispatchRequest(runtime, 'sync', { connectorId: 'mock' }, sender, trusted)).toMatchObject({ ok: true });
    runtime.snapshot.mockImplementation(() => { throw new Error('synthetic-secret-marker'); });
    const response = await dispatchRequest(runtime, 'snapshot', {}, sender, trusted);
    expect(response).toMatchObject({ ok: false, error: { code: 'INTERNAL' } });
    expect(JSON.stringify(response)).not.toContain('synthetic-secret-marker');
  });
  it('分析必须指定合法 ID，查询拒绝额外字段', () => {
    expect(requestSchemas.analyze.safeParse({ eventId: '../../file' }).success).toBe(false);
    expect(requestSchemas.snapshot.safeParse({ tool: 'shell' }).success).toBe(false);
  });
});
