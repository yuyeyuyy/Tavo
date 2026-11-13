import { describe, expect, it, vi } from 'vitest';
import { snapshotSchema, type WorkspaceOperation } from '@tavo/shared';
import { dispatchWorkspaceRequest } from '../../apps/desktop/src/main/ipc';

const trusted = 'file:///app/renderer/index.html'; const sender = { url: trusted, isMainFrame: true, isOwner: true };
function runtime() { const snapshot = snapshotSchema.parse({ connectors: [], accounts: [], tasks: [], events: [] }); return { snapshot: vi.fn(() => snapshot), analyze: vi.fn(async () => snapshot), execute: vi.fn(async () => snapshot) }; }
describe('Phase 1 IPC 最小权限', () => {
  it.each([
    ['addConnection', { connectorId: 'mock', externalAccountId: 'a', displayName: 'A', secret: 'synthetic' }],
    ['syncConnection', { connectionId: '../path' }], ['disconnect', { connectionId: '' }],
    ['updateSettings', { autoSync: true, syncIntervalSeconds: 0, startMinimized: false }],
    ['configureMock', { connectionId: 'a', config: { balance: '0' } }],
  ] as [WorkspaceOperation, unknown][])('%s 拒绝非法参数', async (operation, input) => {
    const target = runtime(); expect(await dispatchWorkspaceRequest(target, operation, input, sender, trusted)).toMatchObject({ ok: false, error: { code: 'INVALID_INPUT' } }); expect(target.execute).not.toHaveBeenCalled();
  });
  it('合法请求经过 controller，其他窗口无法操作', async () => {
    const target = runtime(); expect(await dispatchWorkspaceRequest(target, 'syncConnection', { connectionId: 'a' }, sender, trusted)).toMatchObject({ ok: true });
    expect(target.execute).toHaveBeenCalledWith('syncConnection', { connectionId: 'a' }); target.execute.mockClear();
    expect(await dispatchWorkspaceRequest(target, 'removeConnection', { connectionId: 'a' }, { ...sender, isOwner: false }, trusted)).toMatchObject({ ok: false, error: { code: 'FORBIDDEN' } }); expect(target.execute).not.toHaveBeenCalled();
  });
});
