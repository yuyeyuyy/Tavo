import { afterEach, describe, expect, it } from 'vitest';
import { assertConnectionTransition } from '@tavo/core';
import { fixture, type Fixture } from './fixture';
import { connectionSchema } from '@tavo/shared';

describe('账户实例与凭据生命周期', () => {
  let f: Fixture; afterEach(async () => { await f?.close(); });
  it('同连接器支持两个独立账户；重复连接被拒绝', async () => {
    f = await fixture(); const a = await f.add(); const b = await f.add('demo-b');
    expect(a.id).not.toBe(b.id); expect(a.credentialRef).not.toBe(b.credentialRef);
    expect(f.runtime.snapshot().connections.map((c) => c.status)).toEqual(['connected', 'connected']);
    await expect(f.add()).rejects.toThrow('DUPLICATE_CONNECTION');
    const snapshot = JSON.stringify(f.runtime.snapshot()); expect(snapshot).not.toContain('credentialRef'); expect(snapshot).not.toContain('mock-only:');
  });
  it('断开删除密文，重连更新凭据，删除后可重新添加同一标识', async () => {
    f = await fixture(); const a = await f.add(); const first = await f.credentials.get(a.credentialRef);
    await f.runtime.execute('reconnect', { connectionId: a.id }); expect(await f.credentials.get(a.credentialRef)).not.toBe(first);
    await f.runtime.execute('disconnect', { connectionId: a.id });
    expect(f.storage.workspace.getConnection(a.id)?.status).toBe('disconnected');
    expect(f.storage.workspace.ciphertexts.read(a.credentialRef)).toBeUndefined();
    expect(() => f.runtime.syncEngine.enqueue(a.id)).toThrow('INVALID_STATE');
    await f.runtime.execute('reconnect', { connectionId: a.id }); await f.sync(a.id);
    await f.runtime.execute('removeConnection', { connectionId: a.id });
    expect(f.runtime.snapshot().connections).toEqual([]); expect(f.storage.workspace.getData(a.id)).toBeUndefined();
    expect(f.runtime.snapshot().jobs).toHaveLength(1); expect(f.runtime.snapshot().businessEvents.some((e) => e.eventType === 'account.disconnected')).toBe(true);
    expect((await f.add()).id).not.toBe(a.id);
  });
  it('系统密钥库不可用时添加失败，账户不会伪装为已连接', async () => {
    f = await fixture(); f.provider.available = false;
    await expect(f.add()).rejects.toThrow('CREDENTIAL_UNAVAILABLE');
    expect(f.runtime.snapshot().connections[0]).toMatchObject({ status: 'error', credentialConfigured: false, errorCode: 'CREDENTIAL_UNAVAILABLE' });
    expect(f.storage.workspace.ciphertexts.read(f.storage.workspace.listConnections()[0]!.credentialRef)).toBeUndefined();
  });
  it('拒绝不允许的状态跳转', () => {
    expect(() => assertConnectionTransition('disconnected', 'connected')).toThrow('INVALID_STATE');
    expect(() => assertConnectionTransition('expired', 'connected')).toThrow('INVALID_STATE');
    expect(() => assertConnectionTransition('connecting', 'connected')).not.toThrow();
  });
  it('已连接状态必须具备凭据，状态和错误信息保持一致', async () => {
    f = await fixture(); const a = f.runtime.snapshot().connections;
    expect(a).toEqual([]); const connection = await f.add();
    expect(connectionSchema.strip().safeParse({ ...connection, status: 'connected', credentialConfigured: false }).success).toBe(false);
    expect(connectionSchema.strip().safeParse({ ...connection, status: 'error', errorCode: null }).success).toBe(false);
  });
  it('连接中崩溃不会留下可用凭据或假连接', async () => {
    f = await fixture(); const a = await f.add(); f.storage.workspace.setConnecting(a.id);
    await f.runtime.accounts.recover();
    expect(f.storage.workspace.getConnection(a.id)).toMatchObject({ status: 'error', errorCode: 'INTERRUPTED', credentialConfigured: false });
    expect(f.storage.workspace.ciphertexts.read(a.credentialRef)).toBeUndefined();
  });
  it('断开清理中断后，启动恢复会继续删除密文并完成删除意图', async () => {
    f = await fixture(); const a = await f.add(); const original = f.credentials.delete.bind(f.credentials);
    f.credentials.delete = async () => { throw new Error('synthetic cleanup failure'); };
    await expect(f.runtime.execute('removeConnection', { connectionId: a.id })).rejects.toThrow('synthetic cleanup failure');
    expect(f.storage.workspace.getConnection(a.id)).toMatchObject({ status: 'disconnected', cleanupPending: true, deleteRequested: true });
    expect(() => f.runtime.syncEngine.enqueue(a.id)).toThrow('INVALID_STATE');
    f.credentials.delete = original; await f.runtime.accounts.recover();
    expect(f.runtime.snapshot().connections).toHaveLength(0); expect(f.storage.workspace.ciphertexts.read(a.credentialRef)).toBeUndefined();
  });
});
