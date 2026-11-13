import type { safeStorage } from 'electron';
import type { EncryptionProvider } from '@tavo/core';
import { AppError } from '@tavo/shared';

type SafeStorage = typeof safeStorage;
const linuxBackends = new Set(['gnome_libsecret', 'kwallet', 'kwallet5', 'kwallet6']);
export class ElectronEncryptionProvider implements EncryptionProvider {
  constructor(private readonly api: SafeStorage, private readonly platform: NodeJS.Platform = process.platform) {}
  async status() {
    try {
      if (this.platform === 'linux') {
        const backend = this.api.getSelectedStorageBackend();
        return { available: linuxBackends.has(backend) && this.api.isEncryptionAvailable(), backend };
      }
      if (!['darwin', 'win32'].includes(this.platform)) return { available: false, backend: 'unsupported' };
      return { available: await this.api.isAsyncEncryptionAvailable(), backend: this.platform === 'darwin' ? 'macOS Keychain' : 'Windows DPAPI' };
    } catch { return { available: false, backend: 'unavailable' }; }
  }
  private async available() { if (!(await this.status()).available) throw new AppError('CREDENTIAL_UNAVAILABLE'); }
  async encrypt(secret: string): Promise<Uint8Array> {
    await this.available();
    return this.platform === 'linux' ? this.api.encryptString(secret) : this.api.encryptStringAsync(secret);
  }
  async decrypt(ciphertext: Uint8Array) {
    await this.available();
    return this.platform === 'linux' ? { result: this.api.decryptString(Buffer.from(ciphertext)), shouldReEncrypt: false }
      : this.api.decryptStringAsync(Buffer.from(ciphertext));
  }
}
