"""Make Zeron's source updater fail closed under Nix ownership."""

import argparse
from dataclasses import dataclass
from pathlib import Path

_UPDATE_SOURCE = Path("crates/update/src/lib.rs")
_PATCH_SENTINEL = (
    'const NIX_MANAGED: bool = option_env!("ZERON_NIX_MANAGED").is_some();'
)


@dataclass(frozen=True, slots=True)
class _SourcePatch:
    old: str
    new: str


# Ownership scaffolding shared by both supported updater layouts: the
# sentinel header, the headless apply hook, and the relaunch, auto-update,
# background-task, and install guards.
_OWNERSHIP_HEADER = _SourcePatch(
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
_APPLY_HEADLESS = _SourcePatch(
    """pub fn apply_headless(app_root: &Path, version: &str) -> anyhow::Result<()> {
    #[cfg(unix)]
""",
    """pub fn apply_headless(app_root: &Path, version: &str) -> anyhow::Result<()> {
    ensure_updates_mutable()?;
    #[cfg(unix)]
""",
)
_SHARED_TAIL = (
    _SourcePatch(
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
    _SourcePatch(
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
    _SourcePatch(
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
    _SourcePatch(
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
    _SourcePatch(
        """pub async fn fetch_latest(edge_url: &str) -> anyhow::Result<Manifest> {
    let base = release_base(edge_url)?;
""",
        """pub async fn fetch_latest(edge_url: &str) -> anyhow::Result<Manifest> {
    ensure_updates_mutable()?;
    let base = release_base(edge_url)?;
""",
    ),
    _SourcePatch(
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
    _SourcePatch(
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
    _SourcePatch(
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
    _SourcePatch(
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
    _SourcePatch(
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
    _SourcePatch(
        """pub async fn fetch_latest(edge_url: &str) -> anyhow::Result<Manifest> {
    let base = edge_url.trim_end_matches('/');
""",
        """pub async fn fetch_latest(edge_url: &str) -> anyhow::Result<Manifest> {
    ensure_updates_mutable()?;
    let base = edge_url.trim_end_matches('/');
""",
    ),
    _SourcePatch(
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
    _SourcePatch(
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
    _SourcePatch(
        """pub fn restart_service() -> anyhow::Result<()> {
    if cfg!(target_os = "macos") {
""",
        """pub fn restart_service() -> anyhow::Result<()> {
    ensure_updates_mutable()?;
    if cfg!(target_os = "macos") {
""",
    ),
    _SourcePatch(
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
    _SourcePatch(
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


def _matching_patch_group(
    source: str,
) -> tuple[str, tuple[_SourcePatch, ...]]:
    """Return the first anchor group whose anchors each match exactly once."""
    groups = (
        ("primary", _PATCHES),
        ("fallback", _PATCHES_FALLBACK),
    )
    for label, patches in groups:
        if all(source.count(patch.old) == 1 for patch in patches):
            return label, patches
    msg = "no Zeron updater anchor set matches this source tree"
    raise RuntimeError(msg)


def patch_tree(source_root: Path) -> None:
    """Disable every update network, staging, apply, and relaunch surface."""
    update_path = source_root / _UPDATE_SOURCE
    source = update_path.read_text(encoding="utf-8")
    if _PATCH_SENTINEL in source:
        msg = "Zeron update ownership patch was already applied"
        raise RuntimeError(msg)

    _label, patches = _matching_patch_group(source)
    patched = source
    for patch in patches:
        before, _, after = patched.partition(patch.old)
        patched = f"{before}{patch.new}{after}"
    update_path.write_text(patched, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Apply the Nix update-ownership patch to one Zeron source tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    args = parser.parse_args(argv)
    patch_tree(args.source_root)
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
