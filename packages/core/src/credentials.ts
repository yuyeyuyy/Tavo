import { AppError, idSchema, type CiphertextRepository, type ConnectionRecord, type CredentialAccess, type CredentialStore, type SecureCredentialStore, type WorkspaceStorage } from '@tavo/shared';
import type { ConnectorRegistry } from '@tavo/connectors';

export interface EncryptionProvider {
  status(): Promise<{ available: boolean; backend: string }>;
  encrypt(secret: string): Promise<Uint8Array>;
  decrypt(ciphertext: Uint8Array): Promise<{ result: string; shouldReEncrypt: boolean }>;
}
export class EncryptedCredentialStore implements SecureCredentialStore {
  constructor(private readonly provider: EncryptionProvider, private readonly repository: CiphertextRepository) {}
  async status() { try { return await this.provider.status(); } catch { return { available: false, backend: 'unavailable' }; } }
  private async requireAvailable() { if (!(await this.status()).available) throw new AppError('CREDENTIAL_UNAVAILABLE'); }
  async set(reference: string, secret: string): Promise<void> {
    idSchema.parse(reference); if (!secret || secret.length > 8192) throw new AppError('INVALID_INPUT');
    await this.requireAvailable();
    let ciphertext: Uint8Array;
    try { ciphertext = await this.provider.encrypt(secret); } catch { throw new AppError('CREDENTIAL_UNAVAILABLE'); }
    this.repository.write(reference, ciphertext);
  }
  async get(reference: string): Promise<string> {
    idSchema.parse(reference); await this.requireAvailable();
    const ciphertext = this.repository.read(reference); if (!ciphertext) throw new AppError('CREDENTIAL_UNAVAILABLE');
    let decrypted;
    try { decrypted = await this.provider.decrypt(ciphertext); } catch { throw new AppError('CREDENTIAL_UNAVAILABLE'); }
    if (!decrypted.result || decrypted.result.length > 8192) throw new AppError('CREDENTIAL_UNAVAILABLE');
    if (decrypted.shouldReEncrypt) {
      let rotated: Uint8Array;
      try { rotated = await this.provider.encrypt(decrypted.result); } catch { throw new AppError('CREDENTIAL_UNAVAILABLE'); }
      // OS 密钥轮换不能覆盖并发更新的新凭据，也不能复活已删除的记录。
      this.repository.replace(reference, ciphertext, rotated);
    }
    return decrypted.result;
  }
  // 删除密文不需要解锁密钥库；安全后端不可用时仍可断开。
  async delete(reference: string): Promise<void> { idSchema.parse(reference); this.repository.delete(reference); }
}
export class ScopedCredentialAccess implements CredentialAccess {
  constructor(private readonly store: Pick<CredentialStore, 'get'>, private readonly repository: WorkspaceStorage, private readonly registry: ConnectorRegistry) {}
  async use<T>(connection: ConnectionRecord, operation: (secret: string) => Promise<T>): Promise<T> {
    const current = this.repository.getConnection(connection.id);
    if (!current || current.deletedAt || current.cleanupPending || current.credentialRef !== connection.credentialRef || current.connectorId !== connection.connectorId || !['connecting', 'connected', 'error'].includes(current.status)) throw new AppError('FORBIDDEN');
    const connector = this.registry.get(current.connectorId, 'account');
    if (!connector.info.permissions.includes('read:mock')) throw new AppError('FORBIDDEN');
    const secret = await this.store.get(current.credentialRef);
    const after = this.repository.getConnection(current.id);
    if (!after || after.deletedAt || after.cleanupPending || after.status !== current.status || after.credentialRef !== current.credentialRef) throw new AppError('FORBIDDEN');
    const result = await operation(secret);
    // 防止实现错误把凭据原文作为普通数据返回；不记录异常或原始结果。
    try { if (JSON.stringify(result)?.includes(secret)) throw new AppError('INVALID_DATA'); }
    catch { throw new AppError('INVALID_DATA'); }
    return result;
  }
}
