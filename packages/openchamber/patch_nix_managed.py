"""Make OpenChamber and its bundled OpenCode runtime Nix-owned.

The patch is deliberately anchor checked.  A new upstream release must still
contain every audited mutation surface before the package can be rebuilt.
Each surface is keyed by its audited name and grouped by the source tree that
owns it; OpenChamber and its bundled OpenCode build from separate trees.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from lib.codemods.source_patch import (
    SourceAnchor,
    SourcePatch,
    plan_patches,
    source_tree_parser,
    write_planned,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_MANAGED_MESSAGE = "Updates are managed by Nix."

type Component = Literal["openchamber", "opencode"]

_OPENCHAMBER_PATCHES: Final = {
    "electron-auto-updater-setup": SourcePatch(
        "packages/electron/main.mjs",
        """const setupAutoUpdater = () => {
  if (!app.isPackaged) {
""",
        f"""const setupAutoUpdater = () => {{
  if (app.isPackaged) {{
    log.info('[electron] {_MANAGED_MESSAGE}');
    return;
  }}
  if (!app.isPackaged) {{
""",
    ),
    "electron-ipc-check": SourcePatch(
        "packages/electron/main.mjs",
        """    case 'desktop_check_for_updates': {
      assertUpdaterCapability({ packaged: app.isPackaged });
""",
        f"""    case 'desktop_check_for_updates': {{
      if (app.isPackaged) {{
        return {{
          available: false,
          currentVersion: APP_VERSION,
          version: null,
          body: '{_MANAGED_MESSAGE}',
          date: null,
        }};
      }}
      assertUpdaterCapability({{ packaged: app.isPackaged }});
""",
    ),
    "electron-ipc-download": SourcePatch(
        "packages/electron/main.mjs",
        """    case 'desktop_download_and_install_update':
      assertUpdaterCapability({ packaged: app.isPackaged });
""",
        f"""    case 'desktop_download_and_install_update':
      if (app.isPackaged) {{
        throw new Error('{_MANAGED_MESSAGE}');
      }}
      assertUpdaterCapability({{ packaged: app.isPackaged }});
""",
    ),
    "electron-ipc-restart": SourcePatch(
        "packages/electron/main.mjs",
        """    case 'desktop_restart': {
      const applyUpdate = Boolean(state.pendingUpdate?.downloaded && app.isPackaged);
""",
        """    case 'desktop_restart': {
      // A Nix-store application can restart, but must never call quitAndInstall.
      const applyUpdate = false;
""",
    ),
    "electron-menu-check": SourcePatch(
        "packages/electron/main.mjs",
        """        {
          label: t('checkForUpdates'),
          click: () => dispatchCheckForUpdates(),
        },
""",
        """        {
          label: t('checkForUpdates'),
          click: () => dispatchCheckForUpdates(),
          enabled: !app.isPackaged,
          visible: !app.isPackaged,
        },
""",
        expected_count=2,
    ),
    "electron-asar-native-unpack": SourcePatch(
        "packages/electron/package.json",
        """    "asarUnpack": [
      "node_modules/@openchamber/web/server/built-in-extensions/**/*",
      "node_modules/@openchamber/web/server/lib/git/credential-helper.js",
      "node_modules/@openchamber/web/server/lib/git/ssh-wrapper.js",
      "node_modules/@openchamber/web/server/lib/git/repository-credential-helper.js"
    ],
""",
        """    "asarUnpack": [
      "node_modules/@openchamber/web/server/built-in-extensions/**/*",
      "node_modules/@openchamber/web/server/lib/git/credential-helper.js",
      "node_modules/@openchamber/web/server/lib/git/ssh-wrapper.js",
      "node_modules/@openchamber/web/server/lib/git/repository-credential-helper.js",
      "**/*.node",
      "**/*.dylib"
    ],
""",
    ),
    "electron-notarization": SourcePatch(
        "packages/electron/package.json",
        '      "notarize": true,\n',
        '      "notarize": false,\n',
    ),
    "electron-source-only-node-pty": SourcePatch(
        "packages/electron/scripts/rebuild-native.mjs",
        """    arch: targetArchitecture.electronBuilder,
    onlyModules: ['node-pty', 'bun-pty'],
""",
        """    arch: targetArchitecture.electronBuilder,
    disablePreGypCopy: true,
    onlyModules: ['node-pty'],
""",
    ),
    "renderer-update-polling": SourcePatch(
        "packages/ui/src/hooks/useUpdatePolling.ts",
        """  React.useEffect(() => {
    const initialDelayMs = 3000;
""",
        """  React.useEffect(() => {
    if (import.meta.env.PROD) {
      return;
    }

    const initialDelayMs = 3000;
""",
    ),
    "renderer-managed-state": SourcePatch(
        "packages/ui/src/stores/useUpdateStore.ts",
        "declare const __APP_VERSION__: string | undefined;\n",
        """declare const __APP_VERSION__: string | undefined;

const NIX_MANAGED = import.meta.env.PROD;
const NIX_MANAGED_MESSAGE = 'Updates are managed by Nix.';
""",
    ),
    "renderer-check": SourcePatch(
        "packages/ui/src/stores/useUpdateStore.ts",
        """  checkForUpdates: async () => {
    const runtime = detectRuntimeType();
""",
        """  checkForUpdates: async () => {
    if (NIX_MANAGED) {
      set({
        checking: false,
        available: false,
        downloaded: false,
        downloading: false,
        error: null,
        info: null,
        nextCheckInSec: null,
      });
      return null;
    }

    const runtime = detectRuntimeType();
""",
    ),
    "renderer-download": SourcePatch(
        "packages/ui/src/stores/useUpdateStore.ts",
        """  downloadUpdate: async () => {
    const { available, runtimeType } = get();
""",
        """  downloadUpdate: async () => {
    if (NIX_MANAGED) {
      set({ error: NIX_MANAGED_MESSAGE });
      return;
    }

    const { available, runtimeType } = get();
""",
    ),
    "renderer-restart": SourcePatch(
        "packages/ui/src/stores/useUpdateStore.ts",
        """  restartToUpdate: async () => {
    const { downloaded, installing, runtimeType } = get();
""",
        """  restartToUpdate: async () => {
    if (NIX_MANAGED) {
      set({ error: NIX_MANAGED_MESSAGE });
      return;
    }

    const { downloaded, installing, runtimeType } = get();
""",
    ),
    "web-update-check": SourcePatch(
        "packages/web/server/lib/opencode/openchamber-routes.js",
        """  app.get('/api/openchamber/update-check', async (req, res) => {
    try {
""",
        f"""  app.get('/api/openchamber/update-check', async (_req, res) => {{
    res.json({{
      available: false,
      managedBy: 'nix',
      message: '{_MANAGED_MESSAGE}',
    }});
    return;

    try {{
""",
    ),
    "web-update-install": SourcePatch(
        "packages/web/server/lib/opencode/openchamber-routes.js",
        """  app.post('/api/openchamber/update-install', async (_req, res) => {
    try {
""",
        f"""  app.post('/api/openchamber/update-install', async (_req, res) => {{
    res.status(409).json({{ error: '{_MANAGED_MESSAGE}', managedBy: 'nix' }});
    return;

    try {{
""",
    ),
    "cli-update-command": SourcePatch(
        "packages/web/bin/lib/commands-update.js",
        """  return async function updateCommand(options = {}) {
    const showOutput = shouldRenderHumanOutput(options);
""",
        f"""  return async function updateCommand(options = {{}}) {{
    throw new Error('{_MANAGED_MESSAGE}');

    const showOutput = shouldRenderHumanOutput(options);
""",
    ),
    "lifecycle-opencode-managed-env": SourcePatch(
        "packages/web/server/lib/opencode/lifecycle.js",
        """        OPENCODE_SERVER_PASSWORD: openCodePassword,
""",
        """        OPENCODE_SERVER_PASSWORD: openCodePassword,
        OPENCODE_DISABLE_AUTOUPDATE: 'true',
        OPENCODE_NIX_MANAGED: '1',
""",
    ),
}

_OPENCHAMBER_ANCHORS: Final = {
    "root-postinstall-download": SourceAnchor(
        "package.json",
        '    "postinstall": "node ./fix-deprecation.js && bun run --cwd packages/sdk '
        "build && bun run extensions:build && "
        'node ./packages/electron/scripts/ensure-electron.mjs --best-effort",\n',
    ),
    "electron-prepare-opencode-cli": SourceAnchor(
        "packages/electron/package.json",
        '    "prepare:opencode-cli": "node ./scripts/prepare-opencode-cli.mjs",\n',
    ),
    "electron-rebuild-native": SourceAnchor(
        "packages/electron/package.json",
        '    "rebuild:native": "node ./scripts/rebuild-native.mjs",\n',
    ),
}

_OPENCODE_PATCHES: Final = {
    "opencode-cli-upgrade": SourcePatch(
        "packages/opencode/src/cli/cmd/upgrade.ts",
        "  handler: async (args: { target?: string; method?: string }) => {\n",
        f"""  handler: async (args: {{ target?: string; method?: string }}) => {{
    if (process.env.OPENCODE_NIX_MANAGED === "1") {{
      throw new Error("{_MANAGED_MESSAGE}")
    }}
""",
    ),
}

_OPENCODE_ANCHORS: Final = {
    "opencode-auto-updater": SourceAnchor(
        "packages/opencode/src/cli/upgrade.ts",
        "  if (config.autoupdate === false || Flag.OPENCODE_DISABLE_AUTOUPDATE) return\n",
    ),
    "opencode-auto-updater-flag": SourceAnchor(
        "packages/core/src/flag/flag.ts",
        '  OPENCODE_DISABLE_AUTOUPDATE: truthy("OPENCODE_DISABLE_AUTOUPDATE"),\n',
    ),
}

_OPENCODE_V2_PATCHES: Final = {
    "opencode-cli-upgrade": SourcePatch(
        "packages/cli/src/commands/handlers/upgrade.ts",
        '    function* (input) {\n      intro("Upgrade")\n',
        f"""    function* (input) {{
      if (process.env.OPENCODE_NIX_MANAGED === "1") {{
        return yield* Effect.fail(new Error("{_MANAGED_MESSAGE}"))
      }}
      intro("Upgrade")
""",
    ),
    "opencode-service-upgrade": SourcePatch(
        "packages/cli/src/services/updater.ts",
        "  const upgrade = Effect.fnUntraced(function* (method: Method, input: string) {\n",
        f"""  const upgrade = Effect.fnUntraced(function* (method: Method, input: string) {{
    if (process.env.OPENCODE_NIX_MANAGED === "1") {{
      return yield* Effect.fail(new Error("{_MANAGED_MESSAGE}"))
    }}
""",
    ),
}

_OPENCODE_V2_ANCHORS: Final = {
    "opencode-auto-updater": SourceAnchor(
        "packages/cli/src/services/updater.ts",
        '    if (OPENCODE_LOCAL || ["1", "true"].includes('
        'process.env.OPENCODE_DISABLE_AUTOUPDATE?.toLowerCase() ?? "")) {\n',
    ),
}


def _opencode_v2_layout(root: Path) -> bool:
    """Select the Effect updater only when that service is in the pin.

    OpenCode 1.18 ships ``packages/cli/package.json`` without
    ``packages/cli/src/services/updater.ts``. Treating the package file as the
    v2 layout makes the patcher read a file the pin does not contain.
    """
    return (root / "packages/cli/src/services/updater.ts").is_file()


def _surfaces(
    component: Component,
    root: Path,
) -> tuple[Mapping[str, SourcePatch], Mapping[str, SourceAnchor]]:
    if component == "openchamber":
        return _OPENCHAMBER_PATCHES, _OPENCHAMBER_ANCHORS
    if _opencode_v2_layout(root):
        return _OPENCODE_V2_PATCHES, _OPENCODE_V2_ANCHORS
    return _OPENCODE_PATCHES, _OPENCODE_ANCHORS


def _patch_selected(roots: Mapping[Component, Path], *, check: bool) -> None:
    pending = {}
    for component, root in roots.items():
        patches, anchors = _surfaces(component, root)
        pending |= plan_patches(root, patches.values(), anchors=anchors.values())
    if not check:
        write_planned(pending)


def patch_component(
    component: Component,
    source_root: Path,
    *,
    check: bool = False,
) -> None:
    """Validate and patch one source tree for its package-local derivation."""
    _patch_selected({component: source_root}, check=check)


def patch_trees(
    openchamber_root: Path,
    opencode_root: Path,
    *,
    check: bool = False,
) -> None:
    """Validate and patch both exact source trees, or only dry-run with ``check``."""
    _patch_selected(
        {"openchamber": openchamber_root, "opencode": opencode_root},
        check=check,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Apply or dry-run the Nix ownership patch."""
    parser = source_tree_parser(__doc__)
    parser.add_argument("opencode_root", nargs="?", type=Path)
    parser.add_argument("--component", choices=("openchamber", "opencode"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.component is not None:
        if args.opencode_root is not None:
            parser.error("a component patch accepts exactly one source root")
        patch_component(args.component, args.source_root, check=args.check)
    else:
        if args.opencode_root is None:
            parser.error("the complete patch check requires both source roots")
        patch_trees(args.source_root, args.opencode_root, check=args.check)
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised by the Nix build
    raise SystemExit(main())
