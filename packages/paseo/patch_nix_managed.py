"""Make every Paseo self-mutation surface fail closed under Nix ownership.

Every replacement is exact and count checked.  A new upstream release must be
audited again instead of silently retaining an updater or installer path.
"""

import argparse
import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from lib.exact_text_patch import ExactTextPatch, plan_exact_text_patches

if TYPE_CHECKING:
    from collections.abc import Sequence

_MANAGED_MESSAGE = "Updates are managed by Nix."
_CLAUDE_PROVIDER_PATH = "packages/server/src/server/agent/providers/claude/agent.ts"
_CLAUDE_PROVIDER_SOURCE_DIGEST = (
    "0a5062a28d1a2e54017b62a3de46f15a4eadb37f5c6f2e9b15d93b99c85019e6"
)
_CLAUDE_DEFAULT_BINARY = 'defaultBinary: "claude",'
_CLAUDE_DEFAULT_BINARY_COUNT = 4
_CLAUDE_RESOLVER_FUNCTION = (
    """\
async function resolveClaudeBinary(runtimeSettings?: ProviderRuntimeSettings): Promise<string> {
  const launch = await resolveProviderLaunch({
    commandConfig: runtimeSettings?.command,
    defaultBinary: "claude",
  });
  const availability = await checkProviderLaunchAvailable(launch);
  if (availability.available) {
    return availability.resolvedPath ?? launch.command;
  }
  throw new Error(
"""
    '    "Claude binary not found. Install Claude Code '
    "(https://github.com/anthropics/claude-code) and ensure it is available "
    'in your shell PATH.",\n'
    "  );\n"
    "}\n"
)
_CLAUDE_BUILD_OPTIONS_RETURN = "    return base;\n"
_CLAUDE_BUILD_OPTIONS_TAIL = """\
    if (this.claudeSessionId && !this.pendingFreshSessionId) {
      base.resume = this.claudeSessionId;
    }
    if (this.runtimeSettings?.disallowedTools?.length) {
      base.disallowedTools = [
        ...(base.disallowedTools ?? []),
        ...this.runtimeSettings.disallowedTools,
      ];
    }
    return base;
  }

  private buildSettingsOptions(
"""
_CLAUDE_FINAL_EXECUTABLE_HANDOFF = (
    "    base.pathToClaudeCodeExecutable = claudeBinary;\n"
)


@dataclass(frozen=True, slots=True)
class _SourcePatch:
    relative_path: str
    old: str
    new: str
    expected_count: int = 1


@dataclass(frozen=True, slots=True)
class _SourceAnchor:
    relative_path: str
    text: str
    expected_count: int = 1


_PATCHES = (
    _SourcePatch(
        "packages/desktop/src/features/auto-updater.ts",
        "  isPackaged: () => app.isPackaged,\n",
        "  // Nix owns the immutable application bundle and its update lifecycle.\n"
        "  isPackaged: () => false,\n",
    ),
    _SourcePatch(
        "packages/desktop/src/daemon/daemon-manager.ts",
        """    check_app_update: async (args) => {
      const currentVersion = resolveDesktopAppVersion();
      return checkForAppUpdate({
""",
        f"""    check_app_update: async (args) => {{
      const currentVersion = resolveDesktopAppVersion();
      if (app.isPackaged) {{
        return {{
          hasUpdate: false,
          readyToInstall: false,
          currentVersion,
          latestVersion: currentVersion,
          body: null,
          date: null,
          errorMessage: \"{_MANAGED_MESSAGE}\",
        }};
      }}
      return checkForAppUpdate({{
""",
    ),
    _SourcePatch(
        "packages/desktop/src/daemon/daemon-manager.ts",
        """    install_app_update: async (args) => {
      const currentVersion = resolveDesktopAppVersion();
      return downloadAndInstallUpdate(
""",
        f"""    install_app_update: async (args) => {{
      const currentVersion = resolveDesktopAppVersion();
      if (app.isPackaged) {{
        return {{
          installed: false,
          version: currentVersion,
          message: \"{_MANAGED_MESSAGE}\",
        }};
      }}
      return downloadAndInstallUpdate(
""",
    ),
    _SourcePatch(
        "packages/desktop/src/daemon/daemon-manager.ts",
        "    install_cli: () => installCli(),\n",
        f"""    install_cli: () => {{
      if (app.isPackaged) throw new Error(\"{_MANAGED_MESSAGE}\");
      return installCli();
    }},
""",
    ),
    _SourcePatch(
        "packages/desktop/src/main.ts",
        """  installAppUpdateOnQuit: async (signal) => {
    const settings = await getDesktopSettingsStore().get();
""",
        """  installAppUpdateOnQuit: async (signal) => {
    if (app.isPackaged) return false;
    const settings = await getDesktopSettingsStore().get();
""",
    ),
    _SourcePatch(
        "packages/app/src/desktop/updates/use-desktop-app-updater.ts",
        "  const isDesktopApp = shouldShowDesktopUpdateSection();\n",
        """  // Nix-managed desktop bundles are immutable and cannot self-update.
  const isDesktopApp = false;
""",
    ),
    _SourcePatch(
        "packages/desktop/src/integrations/cli-install/install.ts",
        """export async function installCli(): Promise<InstallStatus> {
  const targetPath = getCliTargetPath();
""",
        f"""export async function installCli(): Promise<InstallStatus> {{
  if (app.isPackaged) throw new Error(\"{_MANAGED_MESSAGE}\");
  const targetPath = getCliTargetPath();
""",
    ),
    _SourcePatch(
        "packages/server/src/server/session/daemon/daemon-self-updater.ts",
        """const DESKTOP_MANAGED_UPDATE_ERROR =
  "This daemon is managed by Paseo Desktop. Update Paseo Desktop on the host.";
""",
        f"""const DESKTOP_MANAGED_UPDATE_ERROR =
  \"This daemon is managed by Paseo Desktop. Update Paseo Desktop on the host.\";
const NIX_MANAGED_UPDATE_ERROR = \"{_MANAGED_MESSAGE}\";

function nixOwnsUpdates(): boolean {{
  return true;
}}
""",
    ),
    _SourcePatch(
        "packages/server/src/server/session/daemon/daemon-self-updater.ts",
        """  async update(input: DaemonSelfUpdateInput): Promise<DaemonSelfUpdateResult> {
    if (input.desktopManaged) {
""",
        """  async update(input: DaemonSelfUpdateInput): Promise<DaemonSelfUpdateResult> {
    if (nixOwnsUpdates()) {
      return { success: false, error: NIX_MANAGED_UPDATE_ERROR, newVersion: null };
    }
    if (input.desktopManaged) {
""",
    ),
    _SourcePatch(
        "packages/server/src/server/session/daemon/npm-global-cli.ts",
        "const NPM_INSTALL_TIMEOUT_MS = 300_000;\n",
        "",
    ),
    _SourcePatch(
        "packages/server/src/server/session/daemon/npm-global-cli.ts",
        """  installLatest(): Promise<CommandResult> {
    return this.runCommand("npm", ["install", "-g", `${PASEO_CLI_PACKAGE}@latest`], {
      timeout: NPM_INSTALL_TIMEOUT_MS,
      maxBuffer: NPM_MAX_BUFFER_BYTES,
    });
  }
""",
        f"""  installLatest(): Promise<CommandResult> {{
    return Promise.resolve({{
      exitCode: 1,
      stdout: \"\",
      stderr: \"{_MANAGED_MESSAGE}\",
    }});
  }}
""",
    ),
    _SourcePatch(
        "packages/desktop/electron-builder.yml",
        """asarUnpack:
  - dist/daemon/node-entrypoint-runner.js
  - node_modules/@getpaseo/server/dist/server/terminal/shell-integration/**/*
""",
        """asarUnpack:
  - dist/daemon/node-entrypoint-runner.js
  - node_modules/@getpaseo/server/dist/server/terminal/shell-integration/**/*
  - node_modules/node-pty/**/*
  - node_modules/sherpa-onnx-darwin-arm64/**/*
""",
    ),
    _SourcePatch(
        "packages/desktop/electron-builder.yml",
        "  notarize: true\n",
        "  notarize: false\n",
    ),
)

_ANCHORS = (
    _SourceAnchor(
        "packages/desktop/src/features/auto-updater.ts",
        "    autoUpdater.quitAndInstall(isSilent, isForceRunAfter);\n",
    ),
    _SourceAnchor(
        "packages/app/src/desktop/updates/use-desktop-app-updater.ts",
        '    void checkForUpdates({ intent: "automatic", silent: true });\n',
        expected_count=2,
    ),
    _SourceAnchor(
        "packages/desktop/src/integrations/cli-install/install.ts",
        "  const { shellUpdated } = await ensurePathInShellRc();\n",
    ),
    _SourceAnchor(
        "packages/server/src/server/session/daemon/npm-global-cli.ts",
        'export const PASEO_CLI_PACKAGE = "@getpaseo/cli";\n',
    ),
)


def _validate_anchor(path: Path, text: str, expected_count: int) -> None:
    count = path.read_text(encoding="utf-8").count(text)
    if count != expected_count:
        msg = f"expected {expected_count} managed-source anchor(s) in {path}, found {count}"
        raise RuntimeError(msg)


def _validate_claude_provider_digest(payload: bytes) -> None:
    actual = sha256(payload).hexdigest()
    if actual != _CLAUDE_PROVIDER_SOURCE_DIGEST:
        msg = (
            "Paseo Claude provider source digest drifted: expected "
            f"{_CLAUDE_PROVIDER_SOURCE_DIGEST}, found {actual}"
        )
        raise RuntimeError(msg)


def _patch_claude_provider(source: str, executable: str) -> str:
    if not Path(executable).is_absolute():
        msg = "Claude Code executable must be an absolute path"
        raise ValueError(msg)
    count = source.count(_CLAUDE_RESOLVER_FUNCTION)
    if count != 1:
        msg = f"expected 1 reviewed Claude resolver function, found {count}"
        raise RuntimeError(msg)
    default_count = source.count(_CLAUDE_DEFAULT_BINARY)
    if default_count != _CLAUDE_DEFAULT_BINARY_COUNT:
        msg = (
            f"expected {_CLAUDE_DEFAULT_BINARY_COUNT} reviewed Claude launch defaults, "
            f"found {default_count}"
        )
        raise RuntimeError(msg)
    tail_count = source.count(_CLAUDE_BUILD_OPTIONS_TAIL)
    if tail_count != 1:
        msg = f"expected 1 reviewed Claude buildOptions tail, found {tail_count}"
        raise RuntimeError(msg)
    patched_tail = _CLAUDE_BUILD_OPTIONS_TAIL.replace(
        _CLAUDE_BUILD_OPTIONS_RETURN,
        _CLAUDE_FINAL_EXECUTABLE_HANDOFF + _CLAUDE_BUILD_OPTIONS_RETURN,
    )
    package_default = f"defaultBinary: {json.dumps(executable)},"
    return source.replace(_CLAUDE_DEFAULT_BINARY, package_default).replace(
        _CLAUDE_BUILD_OPTIONS_TAIL,
        patched_tail,
        1,
    )


def patch_tree(
    source_root: Path,
    *,
    claude_code_executable: str,
    check: bool = False,
) -> None:
    """Patch an exact Paseo source tree, or only validate with ``check``."""
    for anchor in _ANCHORS:
        _validate_anchor(
            source_root / anchor.relative_path,
            anchor.text,
            anchor.expected_count,
        )

    exact_patches = tuple(
        ExactTextPatch(
            Path(patch.relative_path),
            patch.old,
            patch.new,
            patch.expected_count,
        )
        for patch in _PATCHES
    )
    originals = {
        path: (source_root / path).read_text(encoding="utf-8")
        for path in dict.fromkeys(patch.path for patch in exact_patches)
    }
    pending = plan_exact_text_patches(
        originals,
        exact_patches,
        mismatch_message=lambda patch, count: (
            f"expected {patch.expected_count} managed-source patch anchor(s) "
            f"in {source_root / patch.path}, found {count}"
        ),
    )

    claude_provider = source_root / _CLAUDE_PROVIDER_PATH
    provider_payload = claude_provider.read_bytes()
    _validate_claude_provider_digest(provider_payload)
    provider_source = provider_payload.decode("utf-8")
    pending[claude_provider] = _patch_claude_provider(
        provider_source,
        claude_code_executable,
    )

    if check:
        return
    for path, source in pending.items():
        (source_root / path).write_text(source, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Apply or dry-run the Nix ownership patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--claude-code-executable", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    patch_tree(
        args.source_root,
        claude_code_executable=args.claude_code_executable,
        check=args.check,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised by the Nix build
    raise SystemExit(main())
