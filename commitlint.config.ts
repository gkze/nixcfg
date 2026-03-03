import type { UserConfig } from "@commitlint/types";

const LEGACY_FLAKE_LOCK_UPDATE = /^flake\.lock: Update(?: \(#\d+\))?$/u;
const LEGACY_NIX_SOURCES_UPDATE = "nix: Update flake.lock and sources";

const config: UserConfig = {
  extends: ["@commitlint/config-conventional"],
  ignores: [
    (message) => {
      const header = message.trim();
      return (
        header === LEGACY_NIX_SOURCES_UPDATE
        || LEGACY_FLAKE_LOCK_UPDATE.test(header)
      );
    },
  ],
};

export default config;
