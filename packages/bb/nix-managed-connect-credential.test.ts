import { describe, expect, it, vi } from "vitest";
import {
  createConnectCredentialCache,
  type ConnectCredentialCacheFs,
} from "../src/connect-credential-cache.js";
import { nixManagedConnectCredentialEncryption } from "../src/nix-managed-connect-credential-encryption.js";

describe("Nix-managed connect credential storage", () => {
  it("preserves cached bytes and keeps persistence unavailable", async () => {
    const preservedBytes = Buffer.from("existing-encrypted-credential");
    const fs: ConnectCredentialCacheFs = {
      readFile: vi.fn(async () => preservedBytes),
      rm: vi.fn(async () => undefined),
      writeFile: vi.fn(async () => undefined),
    };
    const cache = createConnectCredentialCache({
      encryption: nixManagedConnectCredentialEncryption,
      fs,
      userDataPath: "/preserved-user-data",
    });

    expect(cache.canPersist()).toBe(false);
    await expect(cache.read()).resolves.toBeNull();
    await cache.write({
      credential: "credential-that-must-not-be-persisted",
      handle: "local",
      serverUrl: "http://127.0.0.1:38886",
    });

    expect(fs.readFile).not.toHaveBeenCalled();
    expect(fs.rm).not.toHaveBeenCalled();
    expect(fs.writeFile).not.toHaveBeenCalled();
    expect(preservedBytes.toString()).toBe("existing-encrypted-credential");
  });
});
