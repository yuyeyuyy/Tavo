import { afterEach, describe, expect, it, vi } from 'vitest';
import type { safeStorage } from 'electron';
import { ScopedCredentialAccess } from '@tavo/core';
import { publicError } from '@tavo/shared';
import { ElectronEncryptionProvider } from '../../apps/desktop/src/main/security';
import { fixture, type Fixture } from './fixture';

describe('安全凭据适配与授权范围', () => {
  let f: Fixture; afterEach(async () => { await f?.close(); });
  it('支持新增、更新、删除；数据库只收到密文字节', async () => {
    f = await fixture(); const reference = 'credential:test';
    await f.credentials.set(reference, 'synthetic-secret-a');
    expect(await f.credentials.get(reference)).toBe('synthetic-secret-a');
    expect(Buffer.from(f.storage.workspace.ciphertexts.read(reference)!).includes(Buffer.from('synthetic-secret-a'))).toBe(false);
    await f.credentials.set(reference, 'synthetic-secret-b'); expect(await f.credentials.get(reference)).toBe('synthetic-secret-b');
    f.provider.shouldReEncrypt = true; const previous = f.storage.workspace.ciphertexts.read(reference);
    await f.credentials.get(reference); expect(f.storage.workspace.ciphertexts.read(reference)).not.toEqual(previous);
    f.provider.available = false; await expect(f.credentials.get(reference)).rejects.toThrow('CREDENTIAL_UNAVAILABLE');
    await f.credentials.delete(reference); expect(f.storage.workspace.ciphertexts.read(reference)).toBeUndefined();
  });
  it('异常只保留固定错误码，日志没有原始凭据', async () => {
    f = await fixture(); const log = vi.spyOn(console, 'log'); const errors = vi.spyOn(console, 'error');
    try {
      f.provider.encrypt = async () => { throw new Error('synthetic-secret-marker'); };
      const result = await f.credentials.set('test', 'synthetic-secret-marker').catch(publicError);
      expect(JSON.stringify(result)).not.toContain('synthetic-secret-marker'); expect(log).not.toHaveBeenCalled(); expect(errors).not.toHaveBeenCalled();
    } finally { log.mockRestore(); errors.mockRestore(); }
  });
  it('拒绝伪造的凭据引用、断开账户和连接器返回凭据原文', async () => {
    f = await fixture(); const a = await f.add();
    const access = new ScopedCredentialAccess(f.credentials, f.storage.workspace, f.base.connectors);
    await expect(access.use({ ...a, credentialRef: 'other' }, async () => 1)).rejects.toThrow('FORBIDDEN');
    await expect(access.use(a, async (secret) => ({ leaked: secret }))).rejects.toThrow('INVALID_DATA');
    await f.runtime.execute('disconnect', { connectionId: a.id });
    await expect(access.use(a, async () => 1)).rejects.toThrow('FORBIDDEN');
  });
  it.each(['basic_text', 'unknown'])('Linux 后端 %s 必须明确拒绝', async (backend) => {
    const api = { getSelectedStorageBackend: () => backend, isEncryptionAvailable: () => true, encryptString: vi.fn() } as unknown as typeof safeStorage;
    const provider = new ElectronEncryptionProvider(api, 'linux');
    expect((await provider.status()).available).toBe(false); await expect(provider.encrypt('synthetic')).rejects.toThrow('CREDENTIAL_UNAVAILABLE'); expect(api.encryptString).not.toHaveBeenCalled();
  });
  it('Linux 已知系统后端使用匹配的 API，Windows 异步不可用时拒绝', async () => {
    const api = { getSelectedStorageBackend: () => 'gnome_libsecret', isEncryptionAvailable: () => true, encryptString: vi.fn(() => Buffer.from('ciphertext')), isAsyncEncryptionAvailable: async () => false } as unknown as typeof safeStorage;
    expect((await new ElectronEncryptionProvider(api, 'linux').status()).available).toBe(true);
    await new ElectronEncryptionProvider(api, 'linux').encrypt('synthetic'); expect(api.encryptString).toHaveBeenCalledOnce();
    expect((await new ElectronEncryptionProvider(api, 'win32').status()).available).toBe(false);
  });
  it('OS 轮换重加密不会覆盖并发更新的凭据', async () => {
    f = await fixture(); await f.credentials.set('rotation', 'synthetic-old');
    const original = f.provider.decrypt.bind(f.provider); let release!: () => void; let entered!: () => void;
    const ready = new Promise<void>((resolve) => { entered = resolve; });
    f.provider.decrypt = async (bytes) => { const value = await original(bytes); entered(); await new Promise<void>((resolve) => { release = resolve; }); return { ...value, shouldReEncrypt: true }; };
    const reading = f.credentials.get('rotation'); await ready; await f.credentials.set('rotation', 'synthetic-new'); release(); await reading;
    f.provider.decrypt = original; expect(await f.credentials.get('rotation')).toBe('synthetic-new');
  });
  it('等待解密期间断开账户，不再调用连接器回调', async () => {
    f = await fixture(); const a = await f.add(); const original = f.credentials.get.bind(f.credentials);
    let release!: () => void; let entered!: () => void; const ready = new Promise<void>((resolve) => { entered = resolve; });
    f.credentials.get = async (reference) => { const secret = await original(reference); entered(); await new Promise<void>((resolve) => { release = resolve; }); return secret; };
    const callback = vi.fn(async () => 1); const access = new ScopedCredentialAccess(f.credentials, f.storage.workspace, f.base.connectors);
    const reading = access.use(a, callback); const rejected = expect(reading).rejects.toThrow('FORBIDDEN');
    await ready; await f.runtime.execute('disconnect', { connectionId: a.id }); release(); await rejected;
    expect(callback).not.toHaveBeenCalled();
  });
});
