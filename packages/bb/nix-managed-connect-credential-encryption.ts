import type { ConnectCredentialEncryption } from "./connect-credential-cache.js";

function rejectCredentialEncryption(): never {
  throw new Error("Nix-managed bb does not use Electron safeStorage");
}

export const nixManagedConnectCredentialEncryption: ConnectCredentialEncryption = {
  decryptString(_encrypted: Buffer): string {
    return rejectCredentialEncryption();
  },
  encryptString(_plainText: string): Buffer {
    return rejectCredentialEncryption();
  },
  isEncryptionAvailable(): boolean {
    return false;
  },
};
