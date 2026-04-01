import type { UserConfig } from "@commitlint/types";

const LEGACY_FLAKE_LOCK_UPDATE = /^flake\.lock: Update(?: \(#\d+\))?$/u;
const LEGACY_NIX_UPDATE_HEADERS = new Set([
  "nix: Update flake.lock and sources",
  "nix: Update flake.lock, sources, and crate2nix",
]);

const commitHeader = (message: string): string =>
  message.trim().split(/\r?\n/u, 1)[0]?.trim() ?? "";

export default {
  extends: ["@commitlint/config-conventional"],
  ignores: [
    (message) => {
      const header = commitHeader(message);
      return (
        LEGACY_NIX_UPDATE_HEADERS.has(header) ||
        LEGACY_FLAKE_LOCK_UPDATE.test(header)
      );
    },
  ],
} satisfies UserConfig;
