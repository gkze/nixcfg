"""Make OpenChamber and its bundled OpenCode runtime Nix-owned.

The patch is deliberately anchor checked.  A new upstream release must still
contain every audited mutation surface before the package can be rebuilt.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lib.exact_text_patch import ExactTextPatch, plan_exact_text_patches

if TYPE_CHECKING:
    from collections.abc import Sequence

_MANAGED_MESSAGE = "Updates are managed by Nix."


@dataclass(frozen=True, slots=True)
class _SourcePatch:
    surface: str
    component: Literal["openchamber", "opencode"]
    relative_path: str
    old: str
    new: str
    expected_count: int = 1


@dataclass(frozen=True, slots=True)
class _SourceAnchor:
    surface: str
    component: Literal["openchamber", "opencode"]
    relative_path: str
    text: str
    expected_count: int = 1


_PATCHES = (
    _SourcePatch(
        "electron-auto-updater-setup",
        "openchamber",
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
    _SourcePatch(
        "electron-ipc-check",
        "openchamber",
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
    _SourcePatch(
        "electron-ipc-download",
        "openchamber",
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
    _SourcePatch(
        "electron-ipc-restart",
        "openchamber",
        "packages/electron/main.mjs",
        """    case 'desktop_restart': {
      const applyUpdate = Boolean(state.pendingUpdate?.downloaded && app.isPackaged);
""",
        """    case 'desktop_restart': {
      // A Nix-store application can restart, but must never call quitAndInstall.
      const applyUpdate = false;
""",
    ),
    _SourcePatch(
        "electron-menu-check",
        "openchamber",
        "packages/electron/main.mjs",
        """        {
          label: 'Check for Updates',
          click: () => dispatchCheckForUpdates(),
        },
""",
        """        {
          label: 'Check for Updates',
          click: () => dispatchCheckForUpdates(),
          enabled: !app.isPackaged,
          visible: !app.isPackaged,
        },
""",
        expected_count=2,
    ),
    _SourcePatch(
        "electron-asar-native-unpack",
        "openchamber",
        "packages/electron/package.json",
        '    "npmRebuild": false,\n',
        '    "npmRebuild": false,\n    "asarUnpack": ["**/*.node", "**/*.dylib"],\n',
    ),
    _SourcePatch(
        "electron-notarization",
        "openchamber",
        "packages/electron/package.json",
        '      "notarize": true,\n',
        '      "notarize": false,\n',
    ),
    _SourcePatch(
        "electron-source-only-node-pty",
        "openchamber",
        "packages/electron/scripts/rebuild-native.mjs",
        """    arch: targetArchitecture.electronBuilder,
    onlyModules: ['node-pty', 'bun-pty'],
""",
        """    arch: targetArchitecture.electronBuilder,
    disablePreGypCopy: true,
    onlyModules: ['node-pty'],
""",
    ),
    _SourcePatch(
        "renderer-update-polling",
        "openchamber",
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
    _SourcePatch(
        "renderer-managed-state",
        "openchamber",
        "packages/ui/src/stores/useUpdateStore.ts",
        "declare const __APP_VERSION__: string | undefined;\n",
        """declare const __APP_VERSION__: string | undefined;

const NIX_MANAGED = import.meta.env.PROD;
const NIX_MANAGED_MESSAGE = 'Updates are managed by Nix.';
""",
    ),
    _SourcePatch(
        "renderer-check",
        "openchamber",
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
    _SourcePatch(
        "renderer-download",
        "openchamber",
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
    _SourcePatch(
        "renderer-restart",
        "openchamber",
        "packages/ui/src/stores/useUpdateStore.ts",
        """  restartToUpdate: async () => {
    const { downloaded, runtimeType } = get();
""",
        """  restartToUpdate: async () => {
    if (NIX_MANAGED) {
      set({ error: NIX_MANAGED_MESSAGE });
      return;
    }

    const { downloaded, runtimeType } = get();
""",
    ),
    _SourcePatch(
        "web-update-check",
        "openchamber",
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
    _SourcePatch(
        "web-update-install",
        "openchamber",
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
    _SourcePatch(
        "cli-update-command",
        "openchamber",
        "packages/web/bin/lib/commands-update.js",
        """  return async function updateCommand(options = {}) {
    const showOutput = shouldRenderHumanOutput(options);
""",
        f"""  return async function updateCommand(options = {{}}) {{
    throw new Error('{_MANAGED_MESSAGE}');

    const showOutput = shouldRenderHumanOutput(options);
""",
    ),
    _SourcePatch(
        "lifecycle-opencode-managed-env",
        "openchamber",
        "packages/web/server/lib/opencode/lifecycle.js",
        """          OPENCODE_SERVER_PASSWORD: openCodePassword,
""",
        """          OPENCODE_SERVER_PASSWORD: openCodePassword,
          OPENCODE_DISABLE_AUTOUPDATE: 'true',
          OPENCODE_NIX_MANAGED: '1',
""",
    ),
    _SourcePatch(
        "opencode-cli-upgrade",
        "opencode",
        "packages/opencode/src/cli/cmd/upgrade.ts",
        """  handler: async (args: { target?: string; method?: string }) => {
    UI.empty()
""",
        f"""  handler: async (args: {{ target?: string; method?: string }}) => {{
    if (process.env.OPENCODE_NIX_MANAGED === "1") {{
      throw new Error("{_MANAGED_MESSAGE}")
    }}

    UI.empty()
""",
    ),
    _SourcePatch(
        "opencode-global-http-upgrade",
        "opencode",
        "packages/opencode/src/server/routes/instance/httpapi/handlers/global.ts",
        (
            '    const upgrade = Effect.fn("GlobalHttpApi.upgrade")(function* '
            "(ctx: { payload: typeof GlobalUpgradeInput.Type }) {\n"
            "      const method = yield* installation.method()\n"
        ),
        (
            '    const upgrade = Effect.fn("GlobalHttpApi.upgrade")(function* '
            f"(ctx: {{ payload: typeof GlobalUpgradeInput.Type }}) {{\n"
            f"""      if (process.env.OPENCODE_NIX_MANAGED === "1") {{
        return {{
          status: 403,
          body: {{ success: false as const, error: "{_MANAGED_MESSAGE}" }},
        }}
      }}

      const method = yield* installation.method()
"""
        ),
    ),
)

_ANCHORS = (
    _SourceAnchor(
        "root-postinstall-download",
        "openchamber",
        "package.json",
        '    "postinstall": "node ./fix-deprecation.js && patch-package && node '
        './packages/electron/scripts/ensure-electron.mjs --best-effort",\n',
    ),
    _SourceAnchor(
        "electron-prepare-opencode-cli",
        "openchamber",
        "packages/electron/package.json",
        '    "prepare:opencode-cli": "node ./scripts/prepare-opencode-cli.mjs",\n',
    ),
    _SourceAnchor(
        "electron-rebuild-native",
        "openchamber",
        "packages/electron/package.json",
        '    "rebuild:native": "node ./scripts/rebuild-native.mjs",\n',
    ),
    _SourceAnchor(
        "opencode-auto-updater",
        "opencode",
        "packages/opencode/src/cli/upgrade.ts",
        "  if (config.autoupdate === false || Flag.OPENCODE_DISABLE_AUTOUPDATE) return\n",
    ),
)


def _roots(openchamber_root: Path, opencode_root: Path) -> dict[str, Path]:
    return {"openchamber": openchamber_root, "opencode": opencode_root}


def _validate_anchor(path: Path, text: str, expected_count: int) -> None:
    count = path.read_text(encoding="utf-8").count(text)
    if count != expected_count:
        msg = f"expected {expected_count} managed-source anchor(s) in {path}, found {count}"
        raise RuntimeError(msg)


def _patch_selected(
    roots: dict[str, Path],
    components: frozenset[str],
    *,
    check: bool,
) -> None:
    for anchor in (item for item in _ANCHORS if item.component in components):
        path = roots[anchor.component] / anchor.relative_path
        _validate_anchor(path, anchor.text, anchor.expected_count)

    exact_patches = tuple(
        ExactTextPatch(
            roots[patch.component] / patch.relative_path,
            patch.old,
            patch.new,
            patch.expected_count,
        )
        for patch in _PATCHES
        if patch.component in components
    )
    originals = {
        path: path.read_text(encoding="utf-8")
        for path in dict.fromkeys(patch.path for patch in exact_patches)
    }
    pending = plan_exact_text_patches(
        originals,
        exact_patches,
        mismatch_message=lambda patch, count: (
            f"expected {patch.expected_count} managed-source patch anchor(s) "
            f"in {patch.path}, found {count}"
        ),
    )

    if check:
        return
    for path, source in pending.items():
        path.write_text(source, encoding="utf-8")


def patch_component(
    component: Literal["openchamber", "opencode"],
    source_root: Path,
    *,
    check: bool = False,
) -> None:
    """Validate and patch one source tree for its package-local derivation."""
    _patch_selected(
        {"openchamber": source_root, "opencode": source_root},
        frozenset({component}),
        check=check,
    )


def patch_trees(
    openchamber_root: Path,
    opencode_root: Path,
    *,
    check: bool = False,
) -> None:
    """Validate and patch both exact source trees, or only dry-run with ``check``."""
    _patch_selected(
        _roots(openchamber_root, opencode_root),
        frozenset({"openchamber", "opencode"}),
        check=check,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Apply or dry-run the Nix ownership patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
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
