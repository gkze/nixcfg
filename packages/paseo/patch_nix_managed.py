"""Make every Paseo self-mutation surface fail closed under Nix ownership.

Every replacement and structural seam is exact and count checked. Compatible
upstream releases can advance without pinning unrelated provider source bytes.
"""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from lib.codemods.source_patch import (
    SourceAnchor,
    SourcePatch,
    plan_patches,
    source_tree_parser,
    write_planned,
)
from lib.codemods.text import replace_exactly, replace_once

if TYPE_CHECKING:
    from collections.abc import Sequence

_MANAGED_MESSAGE = "Updates are managed by Nix."
_CLAUDE_PROVIDER_PATH = "packages/server/src/server/agent/providers/claude/agent.ts"
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
_CLAUDE_CLIENT_CLASS = "\n\nexport class ClaudeAgentClient implements AgentClient {"
_CLAUDE_CLIENT_RESOLVER_WIRING = """\
    this.queryFactory = options.queryFactory;
    this.resolveBinary = options.resolveBinary ?? (() => resolveClaudeBinary(this.runtimeSettings));
    this.resolveVersion =
"""
_CLAUDE_SESSION_CLASS = "\n\nclass ClaudeAgentSession implements AgentSession {"
_CLAUDE_SESSION_RESOLVER_WIRING = """\
    this.queryFactory = options.queryFactory;
    this.resolveBinary = options.resolveBinary;
"""
# Session construction and model discovery both forward this resolver.
# The discovery call is indented further, and the six-space token still matches.
_CLAUDE_RESOLVER_FORWARDING = "      resolveBinary: this.resolveBinary,\n"
# Current model discovery resolves the Claude Code version through the same
# resolver as the sessions, so it reaches the Nix-provided executable.
_CLAUDE_CATALOG_VERSION_WIRING = "        this.resolveVersion(context?.signal),\n"
_CLAUDE_AVAILABILITY_LAUNCH = """\
  async isAvailable(): Promise<boolean> {
    const launch = await resolveProviderLaunch({
      commandConfig: this.runtimeSettings?.command,
      defaultBinary: "claude",
    });
    const availability = await checkProviderLaunchAvailable(launch);
    return availability.available;
  }
"""
_CLAUDE_DIAGNOSTIC_LAUNCH = """\
  async getDiagnostic(): Promise<{ diagnostic: string }> {
    try {
      const launch = await resolveProviderLaunch({
        commandConfig: this.runtimeSettings?.command,
        defaultBinary: "claude",
      });
      const availability = await checkProviderLaunchAvailable(launch);
"""
_CLAUDE_VERSION_LAUNCH = """\
export async function resolveClaudeCodeVersion(
  runtimeSettings?: ProviderRuntimeSettings,
  signal?: AbortSignal,
): Promise<string> {
  const launch = await resolveProviderLaunch({
    commandConfig: runtimeSettings?.command,
    defaultBinary: "claude",
  });
  const availability = await checkProviderLaunchAvailable(launch);
"""
_CLAUDE_BUILD_OPTIONS_SIGNATURE = (
    "\n\n  private async buildOptions(permissionMode: PermissionMode): "
    "Promise<ClaudeOptions> {"
)
_CLAUDE_BINARY_RESOLUTION = "    const claudeBinary = await this.resolveBinary();\n"
_CLAUDE_OPTIONS_HANDOFF = "      pathToClaudeCodeExecutable: claudeBinary,\n"


_PATCHES = (
    SourcePatch(
        "packages/desktop/src/features/auto-updater.ts",
        "  isPackaged: () => app.isPackaged,\n",
        "  // Nix owns the immutable application bundle and its update lifecycle.\n"
        "  isPackaged: () => false,\n",
    ),
    SourcePatch(
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
    SourcePatch(
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
    SourcePatch(
        "packages/desktop/src/daemon/daemon-manager.ts",
        "    install_cli: () => installCli(),\n",
        f"""    install_cli: () => {{
      if (app.isPackaged) throw new Error(\"{_MANAGED_MESSAGE}\");
      return installCli();
    }},
""",
    ),
    SourcePatch(
        "packages/desktop/src/main.ts",
        """  installAppUpdateOnQuit: async (signal) => {
    const settings = await getDesktopSettingsStore().get();
""",
        """  installAppUpdateOnQuit: async (signal) => {
    if (app.isPackaged) return false;
    const settings = await getDesktopSettingsStore().get();
""",
    ),
    SourcePatch(
        "packages/app/src/desktop/updates/use-desktop-app-updater.ts",
        "  const isDesktopApp = shouldShowDesktopUpdateSection();\n",
        """  // Nix-managed desktop bundles are immutable and cannot self-update.
  const isDesktopApp = false;
""",
    ),
    SourcePatch(
        "packages/desktop/src/integrations/cli-install/install.ts",
        """export async function installCli(): Promise<InstallStatus> {
  const targetPath = getCliTargetPath();
""",
        f"""export async function installCli(): Promise<InstallStatus> {{
  if (app.isPackaged) throw new Error(\"{_MANAGED_MESSAGE}\");
  const targetPath = getCliTargetPath();
""",
    ),
    SourcePatch(
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
    SourcePatch(
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
    SourcePatch(
        "packages/server/src/server/session/daemon/npm-global-cli.ts",
        "const NPM_INSTALL_TIMEOUT_MS = 300_000;\n",
        "",
    ),
    SourcePatch(
        "packages/server/src/server/session/daemon/npm-global-cli.ts",
        """  installLatest(options: NpmGlobalOptions = {}): Promise<CommandResult> {
    const prefixArgs = options.prefix ? ["--prefix", options.prefix] : [];
    return this.runCommand("npm", ["install", "-g", `${PASEO_CLI_PACKAGE}@latest`, ...prefixArgs], {
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
    SourcePatch(
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
    SourcePatch(
        "packages/desktop/electron-builder.yml",
        "  notarize: true\n",
        "  notarize: false\n",
    ),
)

_ANCHORS = (
    SourceAnchor(
        "packages/desktop/src/features/auto-updater.ts",
        "    autoUpdater.quitAndInstall(isSilent, isForceRunAfter);\n",
    ),
    SourceAnchor(
        "packages/app/src/desktop/updates/use-desktop-app-updater.ts",
        '    void checkForUpdates({ intent: "automatic", silent: true });\n',
        expected_count=2,
    ),
    SourceAnchor(
        "packages/desktop/src/integrations/cli-install/install.ts",
        "  const { shellUpdated } = await ensurePathInShellRc();\n",
    ),
    SourceAnchor(
        "packages/server/src/server/session/daemon/npm-global-cli.ts",
        'export const PASEO_CLI_PACKAGE = "@getpaseo/cli";\n',
    ),
)


def validate_claude_provider_source(source: str) -> None:
    """Validate only the Claude launch and executable-handoff control flow."""
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

    wiring_anchors = (
        (_CLAUDE_CLIENT_CLASS, 1),
        (_CLAUDE_CLIENT_RESOLVER_WIRING, 1),
        (_CLAUDE_SESSION_CLASS, 1),
        (_CLAUDE_SESSION_RESOLVER_WIRING, 1),
        (_CLAUDE_RESOLVER_FORWARDING, 3),
        (_CLAUDE_CATALOG_VERSION_WIRING, 1),
        (_CLAUDE_AVAILABILITY_LAUNCH, 1),
        (_CLAUDE_DIAGNOSTIC_LAUNCH, 1),
        (_CLAUDE_VERSION_LAUNCH, 1),
        ("resolveProviderLaunch(", 4),
        (_CLAUDE_BUILD_OPTIONS_SIGNATURE, 1),
        (_CLAUDE_BINARY_RESOLUTION, 1),
        (_CLAUDE_OPTIONS_HANDOFF, 1),
    )
    if any(source.count(anchor) != expected for anchor, expected in wiring_anchors):
        msg = "reviewed Claude executable wiring drifted"
        raise RuntimeError(msg)

    build_start = source.index(_CLAUDE_BUILD_OPTIONS_SIGNATURE)
    binary_resolution = source.index(_CLAUDE_BINARY_RESOLUTION, build_start)
    options_handoff = source.index(_CLAUDE_OPTIONS_HANDOFF, binary_resolution)
    tail = source.index(_CLAUDE_BUILD_OPTIONS_TAIL, options_handoff)
    before_tail = source[build_start + len(_CLAUDE_BUILD_OPTIONS_SIGNATURE) : tail]
    build_options_flow = source[binary_resolution:tail]
    if (
        not build_start < binary_resolution < options_handoff < tail
        or re.search(r"\breturn\b", before_tail) is not None
        or build_options_flow.count("pathToClaudeCodeExecutable:") != 1
    ):
        msg = "reviewed Claude executable data flow drifted"
        raise RuntimeError(msg)


def _patch_claude_provider(source: str, executable: str) -> str:
    if not Path(executable).is_absolute():
        msg = "Claude Code executable must be an absolute path"
        raise ValueError(msg)
    validate_claude_provider_source(source)
    patched_tail = replace_once(
        _CLAUDE_BUILD_OPTIONS_TAIL,
        _CLAUDE_BUILD_OPTIONS_RETURN,
        _CLAUDE_FINAL_EXECUTABLE_HANDOFF + _CLAUDE_BUILD_OPTIONS_RETURN,
    )
    package_default = f"defaultBinary: {json.dumps(executable)},"
    return replace_once(
        replace_exactly(
            source,
            _CLAUDE_DEFAULT_BINARY,
            package_default,
            expected_count=_CLAUDE_DEFAULT_BINARY_COUNT,
        ),
        _CLAUDE_BUILD_OPTIONS_TAIL,
        patched_tail,
    )


def patch_tree(
    source_root: Path,
    *,
    claude_code_executable: str,
    check: bool = False,
) -> None:
    """Patch an exact Paseo source tree, or only validate with ``check``."""
    pending = plan_patches(source_root, _PATCHES, anchors=_ANCHORS)
    claude_provider = source_root / _CLAUDE_PROVIDER_PATH
    pending[claude_provider] = _patch_claude_provider(
        claude_provider.read_text(encoding="utf-8"),
        claude_code_executable,
    )
    if not check:
        write_planned(pending)


def main(argv: Sequence[str] | None = None) -> int:
    """Apply or dry-run the Nix ownership patch."""
    parser = source_tree_parser(__doc__)
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
