#!/usr/bin/env bash

patch_opencode_desktop_source() {
  local repo_root=$1
  local desktop_package_path=$2

  cd "$repo_root" || return

  # @opencode-ai/script does a semver-based Bun version check at import time,
  # which requires the semver npm package. The filtered bun install with
  # --filter '!./' does not hoist semver@7 into node_modules, so build-time
  # scripts that import @opencode-ai/script read derivation-provided env vars.
  substituteInPlace "$desktop_package_path/scripts/prepare.ts" \
    --replace-fail 'import { Script } from "@opencode-ai/script"' \
    'const Script = { version: process.env.OPENCODE_VERSION ?? "0.0.0" }'

  substituteInPlace packages/opencode/script/build-node.ts \
    --replace-fail 'import { Script } from "@opencode-ai/script"' \
    'const Script = { channel: process.env.OPENCODE_CHANNEL ?? "dev" }'

  # Keep desktop project icons stable unless the user explicitly sets one.
  substituteInPlace "$desktop_package_path/src/main/server.ts" \
    --replace-fail '    OPENCODE_EXPERIMENTAL_ICON_DISCOVERY: "true",' \
    '    OPENCODE_EXPERIMENTAL_ICON_DISCOVERY: "false",'

  # Keep the packaged Electron runtime identity aligned with the Nix-level
  # overrides so the app bundle, userData path, and deep-link scheme agree.
  substituteInPlace "$desktop_package_path/src/main/index.ts" \
    --replace-fail 'const appId = app.isPackaged ? APP_IDS[CHANNEL] : "ai.opencode.desktop.dev"' \
    'const appId = process.env.OPENCODE_APP_ID ?? (app.isPackaged ? APP_IDS[CHANNEL] : "ai.opencode.desktop.dev")' \
    --replace-fail 'app.setName(app.isPackaged ? APP_NAMES[CHANNEL] : "OpenCode Dev")' \
    'app.setName(process.env.OPENCODE_APP_NAME ?? (app.isPackaged ? APP_NAMES[CHANNEL] : "OpenCode Dev"))' \
    --replace-fail 'const urls = argv.filter((arg: string) => arg.startsWith("opencode://"))' \
    'const urls = argv.filter((arg: string) => arg.startsWith((process.env.OPENCODE_PROTOCOL_SCHEME ?? "opencode") + "://"))' \
    --replace-fail 'app.setAsDefaultProtocolClient("opencode")' \
    'app.setAsDefaultProtocolClient(process.env.OPENCODE_PROTOCOL_SCHEME ?? "opencode")'

  substituteInPlace "$desktop_package_path/electron-builder.config.ts" \
    --replace-fail '    name: "OpenCode",' \
    '    name: process.env.OPENCODE_PROTOCOL_NAME ?? "OpenCode",' \
    --replace-fail '    schemes: ["opencode"],' \
    '    schemes: [process.env.OPENCODE_PROTOCOL_SCHEME ?? "opencode"],'

  mkdir -p "$desktop_package_path/native"
}

patch_opencode_desktop_source "$@"
