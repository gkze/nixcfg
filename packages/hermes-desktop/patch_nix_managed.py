"""Make the Hermes Desktop source obey Nix runtime and update ownership."""

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lib.exact_text_patch import ExactTextPatch, plan_exact_text_patches

if TYPE_CHECKING:
    from collections.abc import Sequence


_MANAGED_MESSAGE = "Updates are managed by Nix."
_NIX_STORE_ROOT = Path("/nix/store")


@dataclass(frozen=True, slots=True)
class _SourcePatch:
    relative_path: str
    old: str
    new: str


_PATCHES = (
    _SourcePatch(
        "apps/desktop/electron/main.ts",
        "const IS_PACKAGED = app.isPackaged || Boolean(process.env.HERMES_DESKTOP_IS_PACKAGED)\n",
        """const IS_PACKAGED = app.isPackaged || Boolean(process.env.HERMES_DESKTOP_IS_PACKAGED)
const NIX_MANAGED_HERMES = '@HERMES_EXECUTABLE@'
const NIX_MANAGED_HERMES_VERSION = @HERMES_VERSION@
""",
    ),
    _SourcePatch(
        "apps/desktop/electron/main.ts",
        (
            "function resolveUpdateRoot() {\n"
            "  const candidates = [\n"
            "    process.env.HERMES_DESKTOP_HERMES_ROOT && "
            "path.resolve(process.env.HERMES_DESKTOP_HERMES_ROOT),\n"
            "    !IS_PACKAGED && isHermesSourceRoot(SOURCE_REPO_ROOT) ? "
            "SOURCE_REPO_ROOT : null,\n"
            "    isHermesSourceRoot(ACTIVE_HERMES_ROOT) ? "
            "ACTIVE_HERMES_ROOT : null\n"
            "  ].filter(Boolean)\n"
            "\n"
            "  return candidates.find(c => directoryExists(path.join(c, '.git'))) "
            "|| candidates[0] || ACTIVE_HERMES_ROOT\n"
            "}\n"
        ),
        """function resolveUpdateRoot() {
  return path.dirname(path.dirname(NIX_MANAGED_HERMES))
}
""",
    ),
    _SourcePatch(
        "apps/desktop/electron/main.ts",
        "function resolveHermesVersion() {\n",
        """function resolveHermesVersion() {
  if (IS_PACKAGED) {
    return NIX_MANAGED_HERMES_VERSION
  }

""",
    ),
    _SourcePatch(
        "apps/desktop/electron/main.ts",
        "function resolveHermesBackend(backendArgs) {\n",
        """function resolveHermesBackend(backendArgs) {
  if (!fileExists(NIX_MANAGED_HERMES)) {
    throw new Error(
      `Nix-managed Hermes executable is missing from the runtime closure: ${NIX_MANAGED_HERMES}`
    )
  }

  return {
    label: `Nix-managed Hermes CLI at ${NIX_MANAGED_HERMES}`,
    command: NIX_MANAGED_HERMES,
    args: backendArgs,
    bootstrap: false,
    env: {},
    kind: 'command',
    shell: false
  }

""",
    ),
    _SourcePatch(
        "apps/desktop/electron/main.ts",
        "async function checkUpdates() {\n",
        f"""async function checkUpdates() {{
  if (IS_PACKAGED) {{
    return {{
      supported: false,
      reason: 'nix-managed',
      message: '{_MANAGED_MESSAGE}',
      fetchedAt: Date.now()
    }}
  }}

""",
    ),
    _SourcePatch(
        "apps/desktop/electron/main.ts",
        "async function applyUpdates(opts: { stopSafeBlockers?: boolean } = {}) {\n",
        f"""async function applyUpdates(opts: {{ stopSafeBlockers?: boolean }} = {{}}) {{
  if (IS_PACKAGED) {{
    return {{ ok: false, error: 'nix-managed', message: '{_MANAGED_MESSAGE}' }}
  }}

""",
    ),
    _SourcePatch(
        "apps/desktop/electron/main.ts",
        """ipcMain.handle('hermes:updates:branch:set', async (_event, name) => {
  const branch = typeof name === 'string' && name.trim() ? name.trim() : DEFAULT_UPDATE_BRANCH
  writeDesktopUpdateConfig({ branch })

  return { branch }
})
""",
        """ipcMain.handle('hermes:updates:branch:set', async () => {
  return { branch: DEFAULT_UPDATE_BRANCH }
})
""",
    ),
    _SourcePatch(
        "apps/desktop/electron/main.ts",
        """  const checkForUpdatesItem = {
    label: 'Check for Updates…',
    click: () => sendOpenUpdatesRequested()
  }
""",
        """  const checkForUpdatesItem = {
    label: 'Check for Updates…',
    click: () => sendOpenUpdatesRequested(),
    visible: !IS_PACKAGED
  }
""",
    ),
    _SourcePatch(
        "apps/desktop/package.json",
        '    "afterSign": "scripts/notarize.mjs",\n',
        "",
    ),
    _SourcePatch(
        "apps/desktop/src/store/updates.ts",
        """export interface UpdateApplyState {
""",
        """const NIX_MANAGED_CLIENT = import.meta.env.PROD

export interface UpdateApplyState {
""",
    ),
    _SourcePatch(
        "apps/desktop/src/store/updates.ts",
        """    const status = await bridge.check()
    $updateStatus.set(status)
    maybeNotifyUpdateAvailable(status)
    void refreshDesktopVersion()

    return status
""",
        f"""    const status = await bridge.check()
    const effectiveStatus: DesktopUpdateStatus = NIX_MANAGED_CLIENT
      ? {{
          ...status,
          supported: false,
          updateAvailable: false,
          reason: 'nix-managed',
          message: status.message ?? '{_MANAGED_MESSAGE}'
        }}
      : status

    $updateStatus.set(effectiveStatus)
    maybeNotifyUpdateAvailable(effectiveStatus)
    void refreshDesktopVersion()

    return effectiveStatus
""",
    ),
    _SourcePatch(
        "apps/desktop/src/store/updates.ts",
        (
            "export async function applyUpdates(opts: DesktopUpdateApplyOptions = "
            "{}): Promise<DesktopUpdateApplyResult> {\n"
        ),
        (
            "export async function applyUpdates(opts: DesktopUpdateApplyOptions = "
            "{}): Promise<DesktopUpdateApplyResult> {\n"
            "  if (NIX_MANAGED_CLIENT) {\n"
            f"    return {{ ok: false, error: 'nix-managed', message: '{_MANAGED_MESSAGE}' }}\n"
            "  }\n\n"
        ),
    ),
    _SourcePatch(
        "apps/desktop/src/app/settings/about-settings.tsx",
        (
            "        <ListRow\n"
            "          description={a.automaticUpdatesDesc}\n"
            "          hint={a.branchCommit(status?.branch ?? 'unknown', "
            "status?.currentSha?.slice(0, 7) ?? 'unknown')}\n"
            "          title={a.automaticUpdates}\n"
            "        />\n\n"
            "        <UninstallSection />\n"
        ),
        """        {!import.meta.env.PROD && (
          <>
            <ListRow
              description={a.automaticUpdatesDesc}
              hint={a.branchCommit(
                status?.branch ?? 'unknown',
                status?.currentSha?.slice(0, 7) ?? 'unknown'
              )}
              title={a.automaticUpdates}
            />
            <UninstallSection />
          </>
        )}
""",
    ),
    _SourcePatch(
        "apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx",
        """      onSelect: () => openUpdateOverlayFor('client'),
      title: status.tooltip,
      toggleLabel: copy.toggleVersion,
      variant: 'action'
""",
        """      onSelect: import.meta.env.DEV ? () => openUpdateOverlayFor('client') : undefined,
      title: status.tooltip,
      toggleLabel: copy.toggleVersion,
      variant: import.meta.env.DEV ? 'action' : 'text'
""",
    ),
)


def patch_tree(
    source_root: Path,
    hermes_executable: str,
    hermes_version: str,
) -> None:
    """Apply the Nix ownership policy to one unpacked Hermes source tree."""
    normalized_executable = Path(os.path.normpath(hermes_executable))
    try:
        store_relative = normalized_executable.relative_to(_NIX_STORE_ROOT)
    except ValueError:
        store_relative = None
    if (
        not normalized_executable.is_absolute()
        or store_relative is None
        or store_relative == Path()
    ):
        msg = "Hermes executable must be a normalized path under /nix/store"
        raise ValueError(msg)
    if not hermes_version or hermes_version != hermes_version.strip():
        msg = "Hermes version must be a non-empty normalized string"
        raise ValueError(msg)

    exact_patches: list[ExactTextPatch] = []
    for patch in _PATCHES:
        replacement = patch.new.replace(
            "@HERMES_EXECUTABLE@",
            str(normalized_executable),
        ).replace(
            "@HERMES_VERSION@",
            json.dumps(hermes_version),
        )
        exact_patches.append(
            ExactTextPatch(Path(patch.relative_path), patch.old, replacement)
        )

    sources = {
        path: (source_root / path).read_text(encoding="utf-8")
        for path in dict.fromkeys(patch.path for patch in exact_patches)
    }
    pending = plan_exact_text_patches(
        sources,
        exact_patches,
        mismatch_message=lambda patch, count: (
            f"expected one Hermes source match in {source_root / patch.path}, "
            f"found {count}"
        ),
    )

    for path, source in pending.items():
        (source_root / path).write_text(source, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Patch an unpacked source tree from the package build."""
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("hermes_executable")
    parser.add_argument("hermes_version")
    args = parser.parse_args(argv)
    patch_tree(args.source_root, args.hermes_executable, args.hermes_version)
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised through the Nix build
    raise SystemExit(main())
