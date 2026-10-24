"""Make Zeron's source updater fail closed under Nix ownership."""

from pathlib import Path
from typing import TYPE_CHECKING

from lib.codemods.source_patch import (
    SourcePatch,
    apply_patches,
    require_absent,
    run_source_tree_cli,
    select_variant,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

UPDATE_SOURCE = Path("crates/update/src/lib.rs")
_UPDATE_SOURCE = UPDATE_SOURCE.as_posix()
_PATCH_SENTINEL = (
    'const NIX_MANAGED: bool = option_env!("ZERON_NIX_MANAGED").is_some();'
)


def _update(old: str, new: str) -> SourcePatch:
    """Declare one exact single-occurrence edit of the updater crate."""
    return SourcePatch(_UPDATE_SOURCE, old, new)


# Ownership scaffolding shared by the supported updater layouts: the
# sentinel header, the headless apply hook, and the relaunch, auto-update,
# background-task, and install guards.
_OWNERSHIP_HEADER = _update(
    "use tokio::sync::watch;\n",
    """use tokio::sync::watch;

const NIX_MANAGED: bool = option_env!("ZERON_NIX_MANAGED").is_some();
const NIX_MANAGED_MESSAGE: &str = "updates are managed by Nix";

fn ensure_updates_mutable() -> anyhow::Result<()> {
    if NIX_MANAGED {
        bail!("{NIX_MANAGED_MESSAGE}");
    }
    Ok(())
}
""",
)
_APPLY_HEADLESS = _update(
    """pub fn apply_headless(app_root: &Path, version: &str) -> anyhow::Result<()> {
    #[cfg(unix)]
""",
    """pub fn apply_headless(app_root: &Path, version: &str) -> anyhow::Result<()> {
    ensure_updates_mutable()?;
    #[cfg(unix)]
""",
)
_SHARED_TAIL = (
    _update(
        """pub fn relaunch_app_after_exit(bundle: &Path) {
    #[cfg(unix)]
""",
        """pub fn relaunch_app_after_exit(bundle: &Path) {
    if NIX_MANAGED {
        return;
    }
    #[cfg(unix)]
""",
    ),
    _update(
        """fn auto_update_enabled() -> bool {
    std::env::var("ZERON_AUTO_UPDATE")
""",
        """fn auto_update_enabled() -> bool {
    if NIX_MANAGED {
        return false;
    }
    std::env::var("ZERON_AUTO_UPDATE")
""",
    ),
    _update(
        """        let for_loop = updater.clone();
        let task = tokio::spawn(async move { for_loop.check_loop().await });
        *updater.check_task.lock().unwrap() = Some(task);
        updater
""",
        """        if !NIX_MANAGED {
            let for_loop = updater.clone();
            let task = tokio::spawn(async move { for_loop.check_loop().await });
            *updater.check_task.lock().unwrap() = Some(task);
        }
        updater
""",
    ),
    _update(
        """    pub async fn apply(&self) -> anyhow::Result<String> {
        let InstallKind::Managed { app_root } = detect_install() else {
""",
        """    pub async fn apply(&self) -> anyhow::Result<String> {
        ensure_updates_mutable()?;
        let InstallKind::Managed { app_root } = detect_install() else {
""",
    ),
)

# Primary layout: release_base() helper plus explicit platform-guard calls.
_PATCHES = (
    _OWNERSHIP_HEADER,
    _update(
        """pub async fn fetch_latest(edge_url: &str) -> anyhow::Result<Manifest> {
    let base = release_base(edge_url)?;
""",
        """pub async fn fetch_latest(edge_url: &str) -> anyhow::Result<Manifest> {
    ensure_updates_mutable()?;
    let base = release_base(edge_url)?;
""",
    ),
    _update(
        """pub async fn download_release_file(
    edge_url: &str,
    manifest: &Manifest,
    file: &str,
    dest: &Path,
) -> anyhow::Result<()> {
    let url = format!("{}/{file}", release_base(edge_url)?);
""",
        """pub async fn download_release_file(
    edge_url: &str,
    manifest: &Manifest,
    file: &str,
    dest: &Path,
) -> anyhow::Result<()> {
    ensure_updates_mutable()?;
    let url = format!("{}/{file}", release_base(edge_url)?);
""",
    ),
    _update(
        """pub async fn stage_headless(
    edge_url: &str,
    manifest: &Manifest,
    app_root: &Path,
) -> anyhow::Result<PathBuf> {
    // Reject unsupported targets before creating a stage or making a request.
    require_managed_update_platform()?;
    let version = &manifest.version;
""",
        """pub async fn stage_headless(
    edge_url: &str,
    manifest: &Manifest,
    app_root: &Path,
) -> anyhow::Result<PathBuf> {
    // Reject unsupported targets before creating a stage or making a request.
    require_managed_update_platform()?;
    ensure_updates_mutable()?;
    let version = &manifest.version;
""",
    ),
    _APPLY_HEADLESS,
    _update(
        """pub fn restart_service() -> anyhow::Result<()> {
    require_managed_update_platform()?;
    if cfg!(target_os = "macos") {
""",
        """pub fn restart_service() -> anyhow::Result<()> {
    require_managed_update_platform()?;
    ensure_updates_mutable()?;
    if cfg!(target_os = "macos") {
""",
    ),
    _update(
        """pub async fn stage_mac_app(
    edge_url: &str,
    manifest: &Manifest,
    data_dir: &Path,
) -> anyhow::Result<PathBuf> {
    // Reject unsupported targets before creating a stage or making a request.
    require_mac_app_update_platform()?;
    let version = &manifest.version;
""",
        """pub async fn stage_mac_app(
    edge_url: &str,
    manifest: &Manifest,
    data_dir: &Path,
) -> anyhow::Result<PathBuf> {
    // Reject unsupported targets before creating a stage or making a request.
    require_mac_app_update_platform()?;
    ensure_updates_mutable()?;
    let version = &manifest.version;
""",
    ),
    _update(
        """pub fn apply_mac_app(staged: &Path, bundle: &Path) -> anyhow::Result<()> {
    require_mac_app_update_platform()?;
    let parent = bundle
""",
        """pub fn apply_mac_app(staged: &Path, bundle: &Path) -> anyhow::Result<()> {
    require_mac_app_update_platform()?;
    ensure_updates_mutable()?;
    let parent = bundle
""",
    ),
    *_SHARED_TAIL,
)

# Fallback layout: inline URL trimming and no platform-guard calls to anchor.
_PATCHES_FALLBACK = (
    _OWNERSHIP_HEADER,
    _update(
        """pub async fn fetch_latest(edge_url: &str) -> anyhow::Result<Manifest> {
    let base = edge_url.trim_end_matches('/');
""",
        """pub async fn fetch_latest(edge_url: &str) -> anyhow::Result<Manifest> {
    ensure_updates_mutable()?;
    let base = edge_url.trim_end_matches('/');
""",
    ),
    _update(
        """pub async fn download_release_file(
    edge_url: &str,
    manifest: &Manifest,
    file: &str,
    dest: &Path,
) -> anyhow::Result<()> {
    let url = format!("{}/releases/{file}", edge_url.trim_end_matches('/'));
""",
        """pub async fn download_release_file(
    edge_url: &str,
    manifest: &Manifest,
    file: &str,
    dest: &Path,
) -> anyhow::Result<()> {
    ensure_updates_mutable()?;
    let url = format!("{}/releases/{file}", edge_url.trim_end_matches('/'));
""",
    ),
    _update(
        """pub async fn stage_headless(
    edge_url: &str,
    manifest: &Manifest,
    app_root: &Path,
) -> anyhow::Result<PathBuf> {
    let version = &manifest.version;
""",
        """pub async fn stage_headless(
    edge_url: &str,
    manifest: &Manifest,
    app_root: &Path,
) -> anyhow::Result<PathBuf> {
    ensure_updates_mutable()?;
    let version = &manifest.version;
""",
    ),
    _APPLY_HEADLESS,
    _update(
        """pub fn restart_service() -> anyhow::Result<()> {
    if cfg!(target_os = "macos") {
""",
        """pub fn restart_service() -> anyhow::Result<()> {
    ensure_updates_mutable()?;
    if cfg!(target_os = "macos") {
""",
    ),
    _update(
        """pub async fn stage_mac_app(
    edge_url: &str,
    manifest: &Manifest,
    data_dir: &Path,
) -> anyhow::Result<PathBuf> {
    let version = &manifest.version;
""",
        """pub async fn stage_mac_app(
    edge_url: &str,
    manifest: &Manifest,
    data_dir: &Path,
) -> anyhow::Result<PathBuf> {
    ensure_updates_mutable()?;
    let version = &manifest.version;
""",
    ),
    _update(
        """pub fn apply_mac_app(staged: &Path, bundle: &Path) -> anyhow::Result<()> {
    let parent = bundle
""",
        """pub fn apply_mac_app(staged: &Path, bundle: &Path) -> anyhow::Result<()> {
    ensure_updates_mutable()?;
    let parent = bundle
""",
    ),
    *_SHARED_TAIL,
)


# 0.2.94 subscribes before spawning and shares manual/automatic apply admission.
_PATCHES_094 = (
    *_PATCHES[:-2],
    _update(
        """        let checks = updater.check_tx.subscribe();
        let for_loop = updater.clone();
        let task = tokio::spawn(async move { for_loop.check_loop(checks).await });
        *updater.check_task.lock().unwrap() = Some(task);
        updater
""",
        """        if !NIX_MANAGED {
            let checks = updater.check_tx.subscribe();
            let for_loop = updater.clone();
            let task = tokio::spawn(async move { for_loop.check_loop(checks).await });
            *updater.check_task.lock().unwrap() = Some(task);
        }
        updater
""",
    ),
    _update(
        (
            "    async fn apply_inner(&self, require_quiescent: bool)"
            " -> anyhow::Result<Option<String>> {\n"
            "        let InstallKind::Managed { app_root } = detect_install() else {\n"
        ),
        (
            "    async fn apply_inner(&self, require_quiescent: bool)"
            " -> anyhow::Result<Option<String>> {\n"
            "        ensure_updates_mutable()?;\n"
            "        let InstallKind::Managed { app_root } = detect_install() else {\n"
        ),
    ),
)


# 0.2.97 shares the checker between desktop and engine roles and generalizes
# the relaunch helper. Desktop auto-update now defaults to enabled.
_PATCHES_097 = (
    *_PATCHES[:8],
    _update(
        """pub fn relaunch_after_exit(program: &Path, argument: &Path) {
    #[cfg(unix)]
""",
        """pub fn relaunch_after_exit(program: &Path, argument: &Path) {
    if NIX_MANAGED {
        return;
    }
    #[cfg(unix)]
""",
    ),
    _SHARED_TAIL[1],
    _update(
        """pub fn desktop_auto_update_enabled() -> bool {
    std::env::var("ZERON_AUTO_UPDATE")
""",
        """pub fn desktop_auto_update_enabled() -> bool {
    if NIX_MANAGED {
        return false;
    }
    std::env::var("ZERON_AUTO_UPDATE")
""",
    ),
    _update(
        """        let wakes = updater.wake_tx.subscribe();
        let for_loop = updater.clone();
        let task = tokio::spawn(async move { for_loop.check_loop(wakes, initial_delay).await });
        *updater.check_task.lock().unwrap() = Some(task);
        updater
""",
        """        if !NIX_MANAGED {
            let wakes = updater.wake_tx.subscribe();
            let for_loop = updater.clone();
            let task = tokio::spawn(async move { for_loop.check_loop(wakes, initial_delay).await });
            *updater.check_task.lock().unwrap() = Some(task);
        }
        updater
""",
    ),
    _PATCHES_094[-1],
)


# 0.2.115 routes restart_service through the desktop engine's registered
# MANAGED_RESTART before the platform guard. Nix owns that relaunch too, so the
# ownership guard precedes the dispatch rather than the platform check.
_PATCHES_115 = (
    *_PATCHES_097[:5],
    _update(
        """pub fn restart_service() -> anyhow::Result<()> {
    if let Some(restart) = MANAGED_RESTART.get() {
        return restart();
    }
    require_managed_update_platform()?;
""",
        """pub fn restart_service() -> anyhow::Result<()> {
    ensure_updates_mutable()?;
    if let Some(restart) = MANAGED_RESTART.get() {
        return restart();
    }
    require_managed_update_platform()?;
""",
    ),
    *_PATCHES_097[6:],
)


# Supported updater layouts, newest first. Exactly one must match the tree.
_LAYOUTS = {
    "0.2.115": _PATCHES_115,
    "0.2.97": _PATCHES_097,
    "0.2.94": _PATCHES_094,
    "primary": _PATCHES,
    "fallback": _PATCHES_FALLBACK,
}


def _matching_patch_group(source: str) -> tuple[str, tuple[SourcePatch, ...]]:
    """Return the one anchor group whose anchors each match exactly once."""
    matching = [
        (label, patches)
        for label, patches in _LAYOUTS.items()
        if all(source.count(patch.old) == patch.expected_count for patch in patches)
    ]
    if len(matching) != 1:
        msg = "no Zeron updater anchor set matches this source tree"
        raise RuntimeError(msg)
    return matching[0]


def validate_update_source(source: str) -> str:
    """Return the anchor group label ``patch_tree`` would apply to ``source``."""
    return _matching_patch_group(source)[0]


def patch_tree(source_root: Path) -> None:
    """Disable every update network, staging, apply, and relaunch surface."""
    require_absent(
        source_root,
        (_UPDATE_SOURCE,),
        _PATCH_SENTINEL,
        message="Zeron update ownership patch was already applied",
    )
    _layout, patches = select_variant(source_root, _LAYOUTS)
    apply_patches(source_root, patches)


def main(argv: Sequence[str] | None = None) -> int:
    """Apply the Nix update-ownership patch to one Zeron source tree."""
    return run_source_tree_cli(argv, description=__doc__, patch_tree=patch_tree)


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
