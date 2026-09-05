"""Apply the fail-closed Nix ownership policy to Unsloth Desktop source."""

import argparse
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from lib.exact_text_patch import ExactTextPatch, plan_exact_text_patches

_NIX_MODULE = Path("studio/src-tauri/src/nix_managed.rs")
_PATCH_SENTINEL = 'option_env!("UNSLOTH_NIX_BACKEND")'
_CARGO_MANIFEST = Path("studio/src-tauri/Cargo.toml")
_CARGO_LOCK = Path("studio/src-tauri/Cargo.lock")
_DESKTOP_PACKAGE = "unsloth-studio"
_FIX_PATH_ENV_PACKAGE = "fix-path-env"
_FIX_PATH_ENV_URL = "https://github.com/tauri-apps/fix-path-env-rs"
_DESKTOP_VERSION = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?",
    re.ASCII,
)
_FIX_PATH_ENV_SOURCE = re.compile(
    rf"git\+{re.escape(_FIX_PATH_ENV_URL)}"
    r"(?:\?rev=(?P<revision>[0-9a-f]{40}))?#(?P<commit>[0-9a-f]{40})",
    re.ASCII,
)
_NIX_MODULE_SOURCE = """// SPDX-License-Identifier: AGPL-3.0-only
// Nix ownership policy injected from the nixcfg source package.

const MANAGED_MODE: Option<&str> = option_env!("UNSLOTH_NIX_MANAGED");
const BACKEND_BINARY: Option<&str> = option_env!("UNSLOTH_NIX_BACKEND");
const MANAGED_MESSAGE: &str = "this Unsloth installation is managed by Nix";

pub(crate) fn enabled() -> bool {
    MANAGED_MODE == Some("1")
}

pub(crate) fn require_mutable(operation: &str) -> Result<(), String> {
    if enabled() {
        return Err(format!("Cannot {operation}: {MANAGED_MESSAGE}."));
    }
    Ok(())
}

pub(crate) fn backend_binary() -> Result<std::path::PathBuf, String> {
    if !enabled() {
        return Err("Nix-managed backend requested outside managed mode.".to_string());
    }
    let raw = BACKEND_BINARY
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "Nix-managed backend path was not compiled into the app.".to_string())?;
    let path = std::path::PathBuf::from(raw);
    if !path.is_absolute() || !path.is_file() {
        return Err(format!(
            "Nix-managed backend path is not an absolute executable file: {}",
            path.display()
        ));
    }
    Ok(path)
}
"""


@dataclass(frozen=True, slots=True)
class _SourcePatch:
    path: Path
    old: str
    new: str
    expected_matches: int = 1


def _table_span(source: str, header: str) -> tuple[int, int]:
    """Return the unique textual span for one TOML table."""
    header_pattern = re.compile(rf"(?m)^\[{re.escape(header)}\]\s*(?:#.*)?$")
    matches = list(header_pattern.finditer(source))
    if len(matches) != 1:
        msg = f"expected one [{header}] table, found {len(matches)}"
        raise RuntimeError(msg)
    start = matches[0].end()
    next_header = re.search(r"(?m)^\[", source[start:])
    end = len(source) if next_header is None else start + next_header.start()
    return start, end


def _replace_table_assignment(
    source: str,
    *,
    header: str,
    key: str,
    rendered_value: str,
) -> str:
    """Replace one assignment in a structurally selected TOML table."""
    start, end = _table_span(source, header)
    table = source[start:end]
    assignment = re.compile(rf"(?m)^(?P<indent>\s*){re.escape(key)}\s*=.*$")
    matches = list(assignment.finditer(table))
    if len(matches) != 1:
        msg = f"expected one {key} assignment in [{header}], found {len(matches)}"
        raise RuntimeError(msg)
    match = matches[0]
    replacement = f"{match.group('indent')}{key} = {rendered_value}"
    return (
        source[:start]
        + table[: match.start()]
        + replacement
        + table[match.end() :]
        + source[end:]
    )


def _package_table_spans(source: str) -> tuple[tuple[int, int], ...]:
    """Return every ``[[package]]`` span from a Cargo lock file."""
    headers = tuple(re.finditer(r"(?m)^\[\[package\]\]\s*(?:#.*)?$", source))
    return tuple(
        (
            match.start(),
            headers[index + 1].start() if index + 1 < len(headers) else len(source),
        )
        for index, match in enumerate(headers)
    )


def _replace_lock_package_assignment(
    source: str,
    *,
    package_name: str,
    key: str,
    rendered_value: str,
) -> str:
    """Replace one field in the uniquely named Cargo lock package."""
    matches: list[tuple[int, int]] = []
    for start, end in _package_table_spans(source):
        payload = tomllib.loads(source[start:end])
        packages = payload.get("package")
        if (
            isinstance(packages, list)
            and len(packages) == 1
            and isinstance(packages[0], Mapping)
            and packages[0].get("name") == package_name
        ):
            matches.append((start, end))
    if len(matches) != 1:
        msg = (
            f"expected one Cargo.lock package named {package_name}, "
            f"found {len(matches)}"
        )
        raise RuntimeError(msg)
    start, end = matches[0]
    table = source[start:end]
    assignment = re.compile(rf"(?m)^(?P<indent>\s*){re.escape(key)}\s*=.*$")
    fields = list(assignment.finditer(table))
    if len(fields) != 1:
        msg = (
            f"expected one {key} assignment for Cargo.lock package "
            f"{package_name}, found {len(fields)}"
        )
        raise RuntimeError(msg)
    field = fields[0]
    replacement = f"{field.group('indent')}{key} = {rendered_value}"
    return (
        source[:start]
        + table[: field.start()]
        + replacement
        + table[field.end() :]
        + source[end:]
    )


def _lock_packages(lock: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return typed Cargo lock packages."""
    payload = lock.get("package")
    if not isinstance(payload, list) or not all(
        isinstance(entry, Mapping) for entry in payload
    ):
        msg = "Cargo.lock must contain object package entries"
        raise TypeError(msg)
    return list(payload)


def _unique_lock_package(
    packages: list[Mapping[str, object]],
    name: str,
) -> Mapping[str, object]:
    """Return the uniquely named Cargo lock package."""
    matches = [entry for entry in packages if entry.get("name") == name]
    if len(matches) != 1:
        msg = f"Cargo.lock must contain one {name} package, found {len(matches)}"
        raise RuntimeError(msg)
    return matches[0]


def _fix_path_env_revision(
    manifest: Mapping[str, object],
    packages: list[Mapping[str, object]],
) -> str:
    """Derive and cross-check the locked fix-path-env commit."""
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, Mapping):
        msg = "Cargo.toml must contain object dependencies"
        raise TypeError(msg)
    fix_path_env = dependencies.get(_FIX_PATH_ENV_PACKAGE)
    if not isinstance(fix_path_env, Mapping):
        msg = "Cargo.toml fix-path-env dependency must be an inline table"
        raise TypeError(msg)
    if fix_path_env.get("git") != _FIX_PATH_ENV_URL:
        msg = "Cargo.toml fix-path-env dependency uses an unexpected Git source"
        raise RuntimeError(msg)
    if set(fix_path_env) - {"git", "rev"}:
        msg = "Cargo.toml fix-path-env dependency has unsupported source selectors"
        raise RuntimeError(msg)

    lock_source_id = _unique_lock_package(packages, _FIX_PATH_ENV_PACKAGE).get("source")
    if not isinstance(lock_source_id, str):
        msg = "Cargo.lock fix-path-env source must be a string"
        raise TypeError(msg)
    source_match = _FIX_PATH_ENV_SOURCE.fullmatch(lock_source_id)
    if source_match is None:
        msg = (
            "Cargo.lock fix-path-env source is not an immutable supported Git identity"
        )
        raise RuntimeError(msg)
    revision = source_match.group("commit")
    pinned_revision = source_match.group("revision")
    if pinned_revision is not None and pinned_revision != revision:
        msg = "Cargo.lock fix-path-env rev selector disagrees with its locked commit"
        raise RuntimeError(msg)
    manifest_revision = fix_path_env.get("rev")
    if manifest_revision is not None and manifest_revision != revision:
        msg = "Cargo.toml fix-path-env rev selector disagrees with Cargo.lock"
        raise RuntimeError(msg)
    return revision


def _cargo_consistency_replacements(
    source_root: Path,
    desktop_version: str,
) -> dict[Path, str]:
    """Derive Cargo release and Git identities from immutable candidate source."""
    if _DESKTOP_VERSION.fullmatch(desktop_version) is None:
        msg = f"invalid Unsloth desktop version: {desktop_version!r}"
        raise RuntimeError(msg)

    manifest_source = (source_root / _CARGO_MANIFEST).read_text(encoding="utf-8")
    lock_source = (source_root / _CARGO_LOCK).read_text(encoding="utf-8")
    manifest = tomllib.loads(manifest_source)
    lock = tomllib.loads(lock_source)

    package = manifest.get("package")
    if not isinstance(package, Mapping) or package.get("name") != _DESKTOP_PACKAGE:
        msg = f"Cargo.toml must define package {_DESKTOP_PACKAGE}"
        raise RuntimeError(msg)
    manifest_version = package.get("version")
    if not isinstance(manifest_version, str):
        msg = "Cargo.toml package version must be a string"
        raise TypeError(msg)

    packages = _lock_packages(lock)
    desktop_package = _unique_lock_package(packages, _DESKTOP_PACKAGE)
    if desktop_package.get("version") != manifest_version:
        msg = "Cargo.toml and Cargo.lock desktop versions disagree before release stamping"
        raise RuntimeError(msg)
    revision = _fix_path_env_revision(manifest, packages)

    manifest_source = _replace_table_assignment(
        manifest_source,
        header="package",
        key="version",
        rendered_value=json.dumps(desktop_version),
    )
    manifest_source = _replace_table_assignment(
        manifest_source,
        header="dependencies",
        key=_FIX_PATH_ENV_PACKAGE,
        rendered_value=(
            f"{{ git = {json.dumps(_FIX_PATH_ENV_URL)}, rev = {json.dumps(revision)} }}"
        ),
    )
    lock_source = _replace_lock_package_assignment(
        lock_source,
        package_name=_DESKTOP_PACKAGE,
        key="version",
        rendered_value=json.dumps(desktop_version),
    )
    lock_source = _replace_lock_package_assignment(
        lock_source,
        package_name=_FIX_PATH_ENV_PACKAGE,
        key="source",
        rendered_value=json.dumps(f"git+{_FIX_PATH_ENV_URL}?rev={revision}#{revision}"),
    )
    return {_CARGO_MANIFEST: manifest_source, _CARGO_LOCK: lock_source}


def patch_cargo_tree(source_root: Path, desktop_version: str) -> None:
    """Stamp release metadata and lock Git identity without release-specific patches."""
    replacements = _cargo_consistency_replacements(source_root, desktop_version)
    _write_replacements(source_root, replacements)


RUNTIME_ENVIRONMENT = {
    "UNSLOTH_DIFFUSION_ATTENTION_INSTALL": "0",
    "UNSLOTH_DIFFUSION_SD_CPP_INSTALL": "0",
    "UNSLOTH_DISABLE_LLMCOMPRESSOR_MAIN": "1",
    "UNSLOTH_DISABLE_LLM_COMPRESSOR_AUTOINSTALL": "1",
    "UNSLOTH_DISABLE_MLX_AUTOREPAIR": "1",
    "UNSLOTH_DISABLE_UPDATE_CHECK": "1",
    "UNSLOTH_NIX_MANAGED": "1",
    "UNSLOTH_SKIP_NODE_INSTALL": "1",
    "UNSLOTH_STUDIO_SKIP_FAST_PATH_HOOKS": "1",
    "UNSLOTH_STUDIO_SKIP_FLASHATTN_INSTALL": "1",
    "UNSLOTH_STUDIO_SKIP_FLA_INSTALL": "1",
    "UNSLOTH_STUDIO_SKIP_TILELANG_INSTALL": "1",
}


_PATCHES = (
    _SourcePatch(
        Path("studio/src-tauri/tauri.conf.json"),
        """    "beforeBuildCommand": {
      "cwd": "../frontend",
      "script": "npm run build"
    },
""",
        """    "beforeBuildCommand": {
      "cwd": "../frontend",
      "script": "test -f dist/index.html"
    },
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/tauri.macos.conf.json"),
        """    "resources": {
      "../../install.sh": "install.sh"
    },
""",
        "",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/main.rs"),
        "mod native_path_policy;\nmod preflight;\n",
        "mod native_path_policy;\nmod nix_managed;\nmod preflight;\n",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/main.rs"),
        """fn set_launch_at_login(app: tauri::AppHandle, enabled: bool) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
""",
        """fn set_launch_at_login(app: tauri::AppHandle, enabled: bool) -> Result<bool, String> {
    crate::nix_managed::require_mutable("change launch-at-login")?;
    use tauri_plugin_autostart::ManagerExt;
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/main.rs"),
        """fn rewrite_macos_launch_agent(app: &tauri::AppHandle) {
    let Some(home_dir) = dirs::home_dir() else {
""",
        """fn rewrite_macos_launch_agent(app: &tauri::AppHandle) {
    if crate::nix_managed::enabled() {
        return;
    }
    let Some(home_dir) = dirs::home_dir() else {
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/main.rs"),
        """fn reconcile_autostart_entry(app: &tauri::AppHandle) {
    use tauri_plugin_autostart::ManagerExt;
""",
        """fn reconcile_autostart_entry(app: &tauri::AppHandle) {
    if crate::nix_managed::enabled() {
        return;
    }
    use tauri_plugin_autostart::ManagerExt;
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/main.rs"),
        "        .plugin(tauri_plugin_updater::Builder::new().build())\n",
        "        // Nix owns application updates; do not register the Tauri updater plugin.\n",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/desktop_update_policy.rs"),
        """const DESKTOP_UPDATER_MANIFEST_URL: &str =
    "https://github.com/unslothai/unsloth/releases/latest/download/latest.json";
""",
        """const DESKTOP_UPDATER_MANIFEST_URL: &str =
    "nix-managed://updates-disabled";
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/desktop_update_policy.rs"),
        """pub(crate) enum DesktopUpdateMode {
    InApp,
    ManualLinuxPackage,
}
""",
        """pub(crate) enum DesktopUpdateMode {
    InApp,
    ManualLinuxPackage,
    NixManaged,
}
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/desktop_update_policy.rs"),
        """fn desktop_update_mode() -> DesktopUpdateMode {
    #[cfg(target_os = "linux")]
""",
        """fn desktop_update_mode() -> DesktopUpdateMode {
    if crate::nix_managed::enabled() {
        return DesktopUpdateMode::NixManaged;
    }
    #[cfg(target_os = "linux")]
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/desktop_update_policy.rs"),
        """        assert_eq!(
            super::DESKTOP_UPDATER_MANIFEST_URL,
            "https://github.com/unslothai/unsloth/releases/latest/download/latest.json"
        );
""",
        """        assert_eq!(
            super::DESKTOP_UPDATER_MANIFEST_URL,
            "nix-managed://updates-disabled"
        );
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/desktop_updater.rs"),
        """) -> Result<Option<DesktopUpdateMetadata>, String> {
    let app = webview.app_handle().clone();
""",
        """) -> Result<Option<DesktopUpdateMetadata>, String> {
    if crate::nix_managed::enabled() {
        return Ok(None);
    }
    let app = webview.app_handle().clone();
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/commands.rs"),
        """pub async fn start_install(
    app: AppHandle,
    state: tauri::State<'_, install::InstallState>,
    backend_state: tauri::State<'_, BackendState>,
    diagnostics: tauri::State<'_, DiagnosticsState>,
) -> Result<(), String> {
    if has_owned_backend(&backend_state)? {
""",
        """pub async fn start_install(
    app: AppHandle,
    state: tauri::State<'_, install::InstallState>,
    backend_state: tauri::State<'_, BackendState>,
    diagnostics: tauri::State<'_, DiagnosticsState>,
) -> Result<(), String> {
    crate::nix_managed::require_mutable("install the backend")?;
    if has_owned_backend(&backend_state)? {
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/commands.rs"),
        """pub async fn start_backend_update(
    app: AppHandle,
    backend_state: tauri::State<'_, BackendState>,
    shutdown: tauri::State<'_, ShutdownFlag>,
    update_state: tauri::State<'_, update::UpdateState>,
    install_state: tauri::State<'_, install::InstallState>,
    diagnostics: tauri::State<'_, DiagnosticsState>,
) -> Result<(), String> {
    info!("start_backend_update command called");
""",
        """pub async fn start_backend_update(
    app: AppHandle,
    backend_state: tauri::State<'_, BackendState>,
    shutdown: tauri::State<'_, ShutdownFlag>,
    update_state: tauri::State<'_, update::UpdateState>,
    install_state: tauri::State<'_, install::InstallState>,
    diagnostics: tauri::State<'_, DiagnosticsState>,
) -> Result<(), String> {
    crate::nix_managed::require_mutable("update the backend")?;
    info!("start_backend_update command called");
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/commands.rs"),
        """) -> Result<(), String> {
    let force_installer = force_installer.unwrap_or(false);
""",
        """) -> Result<(), String> {
    crate::nix_managed::require_mutable("repair the backend")?;
    let force_installer = force_installer.unwrap_or(false);
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/install.rs"),
        """fn run_install_with_event_mode(
    app: AppHandle,
    state: InstallState,
    diagnostics: DiagnosticsState,
    event_mode: InstallEventMode,
    repair_group_id: Option<String>,
) -> Result<(), String> {
    let attempt = match repair_group_id.as_deref() {
""",
        """fn run_install_with_event_mode(
    app: AppHandle,
    state: InstallState,
    diagnostics: DiagnosticsState,
    event_mode: InstallEventMode,
    repair_group_id: Option<String>,
) -> Result<(), String> {
    crate::nix_managed::require_mutable("install or repair the backend")?;
    let attempt = match repair_group_id.as_deref() {
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/update.rs"),
        """) -> Result<(), String> {
    let attempt = match &kind {
""",
        """) -> Result<(), String> {
    crate::nix_managed::require_mutable("update, repair, or stage the backend")?;
    let attempt = match &kind {
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/process.rs"),
        """pub fn find_unsloth_binary() -> Option<std::path::PathBuf> {
    let home = dirs::home_dir()?;
""",
        """pub fn find_unsloth_binary() -> Option<std::path::PathBuf> {
    if crate::nix_managed::enabled() {
        return crate::nix_managed::backend_binary().ok();
    }
    let home = dirs::home_dir()?;
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/process.rs"),
        """    pub(crate) fn to_command(&self) -> Command {
        let mut cmd = Command::new(&self.program);
        cmd.args(&self.args);
        cmd
    }
""",
        """    pub(crate) fn to_command(&self) -> Command {
        let mut cmd = Command::new(&self.program);
        cmd.args(&self.args);
        if crate::nix_managed::enabled() {
            cmd.env("UNSLOTH_DISABLE_UPDATE_CHECK", "1");
        }
        cmd
    }
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/process.rs"),
        """    let mut cmd = tokio::process::Command::new(&invocation.program);
    cmd.args(&invocation.args);
    // PYTHONHOME / PYTHONPATH left alone, for the reason in the blocking flavour.
""",
        """    let mut cmd = tokio::process::Command::new(&invocation.program);
    cmd.args(&invocation.args);
    if crate::nix_managed::enabled() {
        cmd.env("UNSLOTH_DISABLE_UPDATE_CHECK", "1");
    }
    // PYTHONHOME / PYTHONPATH left alone, for the reason in the blocking flavour.
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/src/process.rs"),
        """pub(crate) fn resolve_backend_binary() -> Result<std::path::PathBuf, String> {
    // In dev mode, check for local repo venv first
""",
        """pub(crate) fn resolve_backend_binary() -> Result<std::path::PathBuf, String> {
    if crate::nix_managed::enabled() {
        return crate::nix_managed::backend_binary();
    }
    // In dev mode, check for local repo venv first
""",
    ),
    _SourcePatch(
        Path("studio/frontend/src/hooks/use-tauri-update.ts"),
        'export type DesktopUpdatePolicyMode = "in_app" | "manual_linux_package";\n',
        """export type DesktopUpdatePolicyMode =
  | "in_app"
  | "manual_linux_package"
  | "nix_managed";
""",
    ),
    _SourcePatch(
        Path("studio/frontend/src/hooks/use-tauri-update.ts"),
        """      const { policy, resolved } = await resolveUpdatePolicy();

      if (policy.mode === "manual_linux_package") {
""",
        """      const { policy, resolved } = await resolveUpdatePolicy();

      if (policy.mode === "nix_managed") {
        updateRef.current = null;
        replaceInfo(null);
        setStatus("idle");
        return;
      }
      if (policy.mode === "manual_linux_package") {
""",
    ),
    _SourcePatch(
        Path("studio/frontend/src/hooks/use-tauri-update.ts"),
        """      const { policy } = await resolveUpdatePolicy();
      if (policy.mode === "manual_linux_package") {
""",
        """      const { policy } = await resolveUpdatePolicy();
      if (policy.mode === "nix_managed") return;
      if (policy.mode === "manual_linux_package") {
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/tauri.conf.json"),
        (
            "    },\n"
            '    "updater": {\n'
            '      "pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6'
            "IDFBQzA4RjczODM0RjE1QjcKUldTM0ZVK0RjNC9BR2t4R0RVaFR5cTkyUlRVQ1FwaGV0Nk04"
            'eWNwWXBhZnlzalJydllmZm1QTS8K",\n'
            '      "endpoints": [\n'
            '        "https://github.com/unslothai/unsloth/releases/latest/download/'
            'latest.json"\n'
            "      ],\n"
            '      "windows": {\n'
            '        "installMode": "passive"\n'
            "      }\n"
            "    }\n"
        ),
        """    }
""",
    ),
    _SourcePatch(
        Path("studio/src-tauri/tauri.conf.json"),
        '    "createUpdaterArtifacts": true,\n',
        '    "createUpdaterArtifacts": false,\n',
    ),
    _SourcePatch(
        Path("studio/src-tauri/capabilities/default.json"),
        '    "updater:default",\n',
        "",
    ),
)


_MANAGED_ERROR = "this Unsloth installation is managed by Nix"
# Release discovery imports this exact patch contract instead of pinning whole files.
OXC_VALIDATOR_PATH = Path("studio/backend/core/data_recipe/oxc-validator/validate.mjs")
OXC_VALIDATOR_PATCH_SEAM = (
    '    const oxlintBin = join(TOOL_DIR, "node_modules", ".bin", "oxlint");\n'
    "    const oxlintArgs = [\n"
)
OXC_VALIDATOR_PATCH_REPLACEMENT = """    const oxlintBin = process.execPath;
    const oxlintArgs = [
      join(TOOL_DIR, "node_modules", "oxlint", "bin", "oxlint"),
"""

_BACKEND_PATCHES = (
    _SourcePatch(
        Path("pyproject.toml"),
        """    "backend/core/data_recipe/oxc-validator/*.json",
    "backend/core/data_recipe/oxc-validator/*.mjs",
""",
        """    "backend/core/data_recipe/oxc-validator/*.json",
    "backend/core/data_recipe/oxc-validator/*.mjs",
    "backend/core/data_recipe/oxc-validator/node_modules/**/*",
""",
    ),
    _SourcePatch(
        OXC_VALIDATOR_PATH,
        OXC_VALIDATOR_PATCH_SEAM,
        OXC_VALIDATOR_PATCH_REPLACEMENT,
    ),
    _SourcePatch(
        Path("unsloth/chat_templates.py"),
        '        os.system("pip -qqq install git+https://github.com/lm-sys/FastChat.git")\n',
        f'        raise RuntimeError("Cannot install FastChat: {_MANAGED_ERROR}.")\n',
        expected_matches=2,
    ),
    _SourcePatch(
        Path("unsloth/save.py"),
        """def install_llama_cpp_clone_non_blocking():
    full_command = [
""",
        f"""def install_llama_cpp_clone_non_blocking():
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install llama.cpp at runtime: {_MANAGED_ERROR}.")
    full_command = [
""",
    ),
    _SourcePatch(
        Path("unsloth/save.py"),
        """def install_llama_cpp_make_non_blocking():
""",
        f"""def install_llama_cpp_make_non_blocking():
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot build llama.cpp at runtime: {_MANAGED_ERROR}.")
""",
    ),
    _SourcePatch(
        Path("unsloth/save.py"),
        """def install_python_non_blocking(packages = []):
    full_command = ["pip", "install"] + packages
""",
        f"""def install_python_non_blocking(packages = []):
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install Python packages at runtime: {_MANAGED_ERROR}.")
    full_command = ["pip", "install"] + packages
""",
    ),
    _SourcePatch(
        Path("unsloth/save.py"),
        """def install_llama_cpp_old(version = -10):
""",
        f"""def install_llama_cpp_old(version = -10):
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install llama.cpp at runtime: {_MANAGED_ERROR}.")
""",
    ),
    _SourcePatch(
        Path("unsloth/save.py"),
        """def install_llama_cpp_blocking(use_cuda = False):
""",
        f"""def install_llama_cpp_blocking(use_cuda = False):
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install llama.cpp at runtime: {_MANAGED_ERROR}.")
""",
    ),
    _SourcePatch(
        Path("studio/install_python_stack.py"),
        """def install_python_stack() -> int:
    global USE_UV, _STEP, _TOTAL, _PROGRESS_LINE_ACTIVE
""",
        f"""def install_python_stack() -> int:
    global USE_UV, _STEP, _TOTAL, _PROGRESS_LINE_ACTIVE
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        _safe_print(
            "Cannot install or repair Python packages: {_MANAGED_ERROR}.",
            file = sys.stderr,
        )
        return 1
""",
    ),
    _SourcePatch(
        Path("studio/install_sd_cpp_prebuilt.py"),
        '''def install(
    *,
    install_dir: Optional[Path] = None,
    accelerator: str = "auto",
    token: Optional[str] = None,
) -> Path:
    """Download + extract the prebuilt for this host. Returns the sd-cli path.

    Resolves against the Unsloth mirror (``DEFAULT_REPO``) first; if the mirror can't
    serve this host (release missing, or a host we don't build) AND the default repo is
    in use, falls back to leejet upstream so native install still works. Raises
    ``RuntimeError`` only when neither source has an asset for the host, or the archive
    has no ``sd-cli``.
    """
    target = install_dir or default_install_dir()
''',
        f'''def install(
    *,
    install_dir: Optional[Path] = None,
    accelerator: str = "auto",
    token: Optional[str] = None,
) -> Path:
    """Download + extract the prebuilt for this host. Returns the sd-cli path.

    Resolves against the Unsloth mirror (``DEFAULT_REPO``) first; if the mirror can't
    serve this host (release missing, or a host we don't build) AND the default repo is
    in use, falls back to leejet upstream so native install still works. Raises
    ``RuntimeError`` only when neither source has an asset for the host, or the archive
    has no ``sd-cli``.
    """
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install package-owned stable-diffusion.cpp: {_MANAGED_ERROR}.")
    target = install_dir or default_install_dir()
''',
    ),
    _SourcePatch(
        Path("studio/install_node_prebuilt.py"),
        (
            "def install_prebuilt(install_dir: Path, *, channel: str, "
            "min_major: int, force: bool) -> int:\n"
            "    host = detect_host()\n"
        ),
        (
            "def install_prebuilt(install_dir: Path, *, channel: str, "
            "min_major: int, force: bool) -> int:\n"
            '    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":\n'
            "        raise RuntimeError("
            f'"Cannot install package-owned Node.js: {_MANAGED_ERROR}."'
            ")\n"
            "    host = detect_host()\n"
        ),
    ),
    _SourcePatch(
        Path("studio/install_llama_prebuilt.py"),
        """def install_prebuilt(
    install_dir: Path,
    llama_tag: str,
    published_repo: str,
    published_release_tag: str,
    *,
    override_has_rocm: bool = False,
    override_rocm_gfx: str | None = None,
    force_cpu: bool = False,
    persist_force_cpu: bool = False,
    llama_backend: str | None = None,
    instruction_cleanup_root: Path | None = None,
) -> None:
    choice: AssetChoice | None = None
""",
        f"""def install_prebuilt(
    install_dir: Path,
    llama_tag: str,
    published_repo: str,
    published_release_tag: str,
    *,
    override_has_rocm: bool = False,
    override_rocm_gfx: str | None = None,
    force_cpu: bool = False,
    persist_force_cpu: bool = False,
    llama_backend: str | None = None,
    instruction_cleanup_root: Path | None = None,
) -> None:
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install package-owned llama.cpp: {_MANAGED_ERROR}.")
    choice: AssetChoice | None = None
""",
    ),
    _SourcePatch(
        Path("studio/install_whisper_prebuilt.py"),
        """def install_prebuilt(
    install_dir: Path,
    *,
    whisper_tag: str = "latest",
    published_repo: str = DEFAULT_PUBLISHED_REPO,
    published_release_tag: str | None = None,
    backend: str | None = "auto",
    has_rocm: bool = False,
    rocm_gfx: str | None = None,
    cpu_fallback: bool = False,
    force: bool = False,
) -> int:
    host = apply_host_overrides(
""",
        f"""def install_prebuilt(
    install_dir: Path,
    *,
    whisper_tag: str = "latest",
    published_repo: str = DEFAULT_PUBLISHED_REPO,
    published_release_tag: str | None = None,
    backend: str | None = "auto",
    has_rocm: bool = False,
    rocm_gfx: str | None = None,
    cpu_fallback: bool = False,
    force: bool = False,
) -> int:
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install package-owned whisper.cpp: {_MANAGED_ERROR}.")
    host = apply_host_overrides(
""",
    ),
    _SourcePatch(
        Path("unsloth_cli/commands/studio.py"),
        """    return _studio_deps.install_state(
""",
        """    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        return {
            "ok": True,
            "manifest_ok": True,
            "deps_ok": True,
            "missing": [],
            "reason": None,
        }
    return _studio_deps.install_state(
""",
    ),
    _SourcePatch(
        Path("unsloth_cli/commands/studio.py"),
        """    studio_venv_dir = STUDIO_HOME / "unsloth_studio"
    in_studio_venv = sys.prefix.startswith(str(studio_venv_dir))
""",
        """    studio_venv_dir = STUDIO_HOME / "unsloth_studio"
    in_studio_venv = (
        os.environ.get("UNSLOTH_NIX_MANAGED") == "1"
        or sys.prefix.startswith(str(studio_venv_dir))
    )
""",
        expected_matches=2,
    ),
    _SourcePatch(
        Path("unsloth_cli/commands/studio.py"),
        """    \"\"\"Run Unsloth setup (called by install.ps1 / install.sh).\"\"\"
    runtime_gate_handoff = _studio_runtime_gate.consume_runtime_gate_handoff()
""",
        f"""    \"\"\"Run Unsloth setup (called by install.ps1 / install.sh).\"\"\"
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        typer.echo("Cannot run Studio setup: {_MANAGED_ERROR}.", err = True)
        raise typer.Exit(1)
    runtime_gate_handoff = _studio_runtime_gate.consume_runtime_gate_handoff()
""",
    ),
    _SourcePatch(
        Path("unsloth_cli/commands/studio.py"),
        """    \"\"\"Update Unsloth Studio dependencies and rebuild.\"\"\"
    # Re-export UNSLOTH_STUDIO_HOME for env-mode installs so the refresh
""",
        f"""    \"\"\"Update Unsloth Studio dependencies and rebuild.\"\"\"
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        typer.echo("Cannot update Studio dependencies: {_MANAGED_ERROR}.", err = True)
        raise typer.Exit(1)
    # Re-export UNSLOTH_STUDIO_HOME for env-mode installs so the refresh
""",
    ),
    _SourcePatch(
        Path("studio/backend/core/training/worker.py"),
        """def _uninstall_package(pypi_name: str, display_name: str) -> bool:
    \"\"\"Remove a distribution. True iff it is gone afterwards.\"\"\"
    if shutil.which("uv"):
""",
        """def _uninstall_package(pypi_name: str, display_name: str) -> bool:
    \"\"\"Remove a distribution. True iff it is gone afterwards.\"\"\"
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.warning("Cannot uninstall %s from the Nix-managed backend", display_name)
        return False
    if shutil.which("uv"):
""",
    ),
    _SourcePatch(
        Path("studio/backend/core/training/worker.py"),
        """def _install_package_wheel_first(
    *, event_queue: Any, import_name: str, display_name: str, pypi_name: str, **kwargs: Any
) -> bool:
    \"\"\"Install a fast-path package, wheel first, and never leave an unusable one behind.

    The two "touch nothing" guards run here, outside the cleanup: an already-working
    package returns before any subprocess, and offline changes nothing. Everything after
    them is an install attempt, so ANY unsuccessful exit -- timeout, failed install, bad
    import -- discards what is left rather than leaving metadata the in-process import
    would pick up. Enforced here rather than at each return because four separate exits
    have now been found that forgot to clean up.
    \"\"\"
    if _is_importable(import_name):
        logger.info("%s already installed", display_name)
        return True

    if _model_offline_mode_enabled():
""",
        """def _install_package_wheel_first(
    *, event_queue: Any, import_name: str, display_name: str, pypi_name: str, **kwargs: Any
) -> bool:
    \"\"\"Install a fast-path package, wheel first, and never leave an unusable one behind.

    The two "touch nothing" guards run here, outside the cleanup: an already-working
    package returns before any subprocess, and offline changes nothing. Everything after
    them is an install attempt, so ANY unsuccessful exit -- timeout, failed install, bad
    import -- discards what is left rather than leaving metadata the in-process import
    would pick up. Enforced here rather than at each return because four separate exits
    have now been found that forgot to clean up.
    \"\"\"
    if _is_importable(import_name):
        logger.info("%s already installed", display_name)
        return True

    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.info("Skipping %s installation in the Nix-managed backend", display_name)
        return False

    if _model_offline_mode_enabled():
""",
    ),
    _SourcePatch(
        Path("studio/backend/core/training/worker.py"),
        """def _attempt_package_install(
    *,
    event_queue: Any,
    import_name: str,
    display_name: str,
    pypi_name: str,
    pypi_version: str | None = None,
    filename_prefix: str | None = None,
    release_tag: str | None = None,
    release_base_url: str | None = None,
    wheel_url_builder: Callable[[dict[str, str] | None], str | None] | None = None,
    pypi_spec: str | None = None,
    pypi_status_message: str | None = None,
) -> bool:
    \"\"\"The install itself. Call it through _install_package_wheel_first, never directly.\"\"\"
    # Set when a wheel installed but would not import; see the uninstall before the fallback.
""",
        """def _attempt_package_install(
    *,
    event_queue: Any,
    import_name: str,
    display_name: str,
    pypi_name: str,
    pypi_version: str | None = None,
    filename_prefix: str | None = None,
    release_tag: str | None = None,
    release_base_url: str | None = None,
    wheel_url_builder: Callable[[dict[str, str] | None], str | None] | None = None,
    pypi_spec: str | None = None,
    pypi_status_message: str | None = None,
) -> bool:
    \"\"\"The install itself. Call it through _install_package_wheel_first, never directly.\"\"\"
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.info("Skipping %s installation in the Nix-managed backend", display_name)
        return False
    # Set when a wheel installed but would not import; see the uninstall before the fallback.
""",
    ),
    _SourcePatch(
        Path("studio/backend/core/training/worker.py"),
        """def _run_pip(cmd: list[str], event_queue: Any, label: str) -> bool:
    \"\"\"Run a pip install and surface success/failure via status events.\"\"\"
    try:
""",
        """def _run_pip(cmd: list[str], event_queue: Any, label: str) -> bool:
    \"\"\"Run a pip install and surface success/failure via status events.\"\"\"
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.info("Skipping %s installation in the Nix-managed backend", label)
        return False
    try:
""",
    ),
    _SourcePatch(
        Path("studio/backend/utils/transformers_version.py"),
        """def _install_to_dir(pkg: str, target_dir: str) -> bool:
    \"\"\"Install a single package into *target_dir*, preferring uv then pip.\"\"\"
    # Try uv first (faster) if on PATH -- do NOT install uv at runtime.
""",
        """def _install_to_dir(pkg: str, target_dir: str) -> bool:
    \"\"\"Install a single package into *target_dir*, preferring uv then pip.\"\"\"
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.warning("Cannot install %s into the Nix-managed backend", pkg)
        return False
    # Try uv first (faster) if on PATH -- do NOT install uv at runtime.
""",
    ),
    _SourcePatch(
        Path("studio/backend/utils/transformers_version.py"),
        """    if _venv_dir_is_valid_and_undamaged(venv_dir, packages):
        return True

    logger.warning("%s not found or incomplete at %s -- installing at runtime", label, venv_dir)
""",
        """    if _venv_dir_is_valid_and_undamaged(venv_dir, packages):
        return True
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.warning("%s is absent from the Nix closure; runtime repair is disabled", label)
        return False

    logger.warning("%s not found or incomplete at %s -- installing at runtime", label, venv_dir)
""",
    ),
    _SourcePatch(
        Path("studio/backend/utils/wheel_utils.py"),
        """def install_wheel(
    wheel_url: str,
    *,
    python_executable: str,
    use_uv: bool,
    uv_needs_system: bool = False,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[tuple[str, subprocess.CompletedProcess[str]]]:
    attempts: list[tuple[str, subprocess.CompletedProcess[str]]] = []
""",
        """def install_wheel(
    wheel_url: str,
    *,
    python_executable: str,
    use_uv: bool,
    uv_needs_system: bool = False,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[tuple[str, subprocess.CompletedProcess[str]]]:
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        _logger.warning("Cannot install wheel into the Nix-managed backend: %s", wheel_url)
        return []
    attempts: list[tuple[str, subprocess.CompletedProcess[str]]] = []
""",
    ),
    _SourcePatch(
        Path("studio/backend/utils/ssm_runtime.py"),
        """def _install_kernel(
    *,
    import_name: str,
    display_name: str,
    pypi_name: str,
    package_version: str,
    release_tag: str,
    release_base_url: str,
    status_cb: StatusCb,
    run: Callable[..., Any],
) -> bool:
    \"\"\"Install one kernel wheel-first, then a HIP-aware PyPI source build. Returns True iff
    importable afterwards; idempotent (no-op when already installed).\"\"\"
    if _is_importable(import_name):
        logger.info("%s already installed", display_name)
        return True

    from utils.utils import hf_env_offline
""",
        """def _install_kernel(
    *,
    import_name: str,
    display_name: str,
    pypi_name: str,
    package_version: str,
    release_tag: str,
    release_base_url: str,
    status_cb: StatusCb,
    run: Callable[..., Any],
) -> bool:
    \"\"\"Install one kernel wheel-first, then a HIP-aware PyPI source build. Returns True iff
    importable afterwards; idempotent (no-op when already installed).\"\"\"
    if _is_importable(import_name):
        logger.info("%s already installed", display_name)
        return True
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.info("Skipping %s installation in the Nix-managed backend", display_name)
        return False

    from utils.utils import hf_env_offline
""",
    ),
    _SourcePatch(
        Path("studio/backend/utils/ssm_runtime.py"),
        """    wants_causal_conv1d = model_wants_causal_conv1d(model_name)
    is_ssm = model_is_ssm(model_name)
""",
        """    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.info("SSM runtime auto-install is disabled for the Nix-managed backend")
        return
    wants_causal_conv1d = model_wants_causal_conv1d(model_name)
    is_ssm = model_is_ssm(model_name)
""",
    ),
    _SourcePatch(
        Path("studio/backend/utils/whisper_cpp_update.py"),
        """def _install_latest(
    install_dir: Path,
    repo: str,
    asset: Optional[str],
    backend: Optional[str],
    script: Path,
    set_progress,
    pin_release_tag: Optional[str] = None,
) -> dict:
    \"\"\"Replace whisper.cpp while the sidecar blocks every new load.\"\"\"
    try:
""",
        f"""def _install_latest(
    install_dir: Path,
    repo: str,
    asset: Optional[str],
    backend: Optional[str],
    script: Path,
    set_progress,
    pin_release_tag: Optional[str] = None,
) -> dict:
    \"\"\"Replace whisper.cpp while the sidecar blocks every new load.\"\"\"
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot update package-owned whisper.cpp: {_MANAGED_ERROR}.")
    try:
""",
    ),
    _SourcePatch(
        Path("studio/backend/cloudflare_tunnel.py"),
        """    existing = find_cloudflared()
    if existing:
        return existing
    asset = _asset_name()
""",
        """    existing = find_cloudflared()
    if existing:
        return existing
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        return None
    asset = _asset_name()
""",
    ),
    _SourcePatch(
        Path("studio/backend/utils/llama_cpp_update.py"),
        """def _start_llama_job(backend_request: Optional[str] = None) -> dict:
    \"\"\"Shared body of start_update() and start_backend_switch().\"\"\"
    if not _claim_operation(backend_request):
""",
        f"""def _start_llama_job(backend_request: Optional[str] = None) -> dict:
    \"\"\"Shared body of start_update() and start_backend_switch().\"\"\"
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        with _job_lock:
            job = dict(_job)
        return {{
            "started": False,
            "reason": "nix_managed",
            "message": "Cannot update package-owned helper binaries: {_MANAGED_ERROR}.",
            "job": job,
        }}
    if not _claim_operation(backend_request):
""",
    ),
    _SourcePatch(
        Path("studio/backend/core/inference/sd_cpp_backend.py"),
        """    if usable and not _accelerator_changed(found, accelerator):
        return found
    if not allow_install:
        return found
    with _install_lock:
""",
        """    if usable and not _accelerator_changed(found, accelerator):
        return found
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        return found if usable else None
    if not allow_install:
        return found
    with _install_lock:
""",
        expected_matches=2,
    ),
    _SourcePatch(
        Path("studio/setup.sh"),
        """set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
""",
        f"""set -euo pipefail

if [ "${{UNSLOTH_NIX_MANAGED:-0}}" = 1 ]; then
    echo "Cannot run Studio setup: {_MANAGED_ERROR}." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
""",
    ),
    _SourcePatch(
        Path("studio/setup.sh"),
        (
            'if [ -d "$_OXC_DIR" ] && '
            '[ "${NODE_SOURCE:-}" != skip ] && '
            "command -v npm &>/dev/null; then\n"
        ),
        (
            'if [ "${UNSLOTH_NIX_MANAGED:-0}" != 1 ] && '
            '[ -d "$_OXC_DIR" ] && '
            '[ "${NODE_SOURCE:-}" != skip ] && '
            "command -v npm &>/dev/null; then\n"
        ),
    ),
    _SourcePatch(
        Path("studio/setup.ps1"),
        """$ErrorActionPreference = "Stop"

# This script is spawned as powershell.exe -- Windows PowerShell 5.1 (see the PSModulePath note
""",
        f"""$ErrorActionPreference = "Stop"

if ($env:UNSLOTH_NIX_MANAGED -eq "1") {{
    throw "Cannot run Studio setup: {_MANAGED_ERROR}."
}}

# This script is spawned as powershell.exe -- Windows PowerShell 5.1 (see the PSModulePath note
""",
    ),
    _SourcePatch(
        Path("studio/setup.ps1"),
        (
            "if ((Test-Path $OxcValidatorDir) -and "
            '$NodeSource -ne "skip" -and '
            "(Get-Command npm -ErrorAction SilentlyContinue)) {\n"
        ),
        (
            'if ($env:UNSLOTH_NIX_MANAGED -ne "1" -and '
            "(Test-Path $OxcValidatorDir) -and "
            '$NodeSource -ne "skip" -and '
            "(Get-Command npm -ErrorAction SilentlyContinue)) {\n"
        ),
    ),
)


def _validate_module_path(source_root: Path) -> Path:
    module_path = source_root / _NIX_MODULE
    if module_path.exists():
        if _PATCH_SENTINEL in module_path.read_text(encoding="utf-8"):
            msg = "Unsloth Nix ownership patch was already applied"
        else:
            msg = f"refusing to replace existing Unsloth source file: {_NIX_MODULE}"
        raise RuntimeError(msg)
    return module_path


def _validated_replacements(
    source_root: Path,
    patches: tuple[_SourcePatch, ...],
) -> dict[Path, str]:
    exact_patches = tuple(
        ExactTextPatch(
            patch.path,
            patch.old,
            patch.new,
            patch.expected_matches,
        )
        for patch in patches
    )
    originals = {
        path: (source_root / path).read_text(encoding="utf-8")
        for path in dict.fromkeys(patch.path for patch in exact_patches)
    }
    return plan_exact_text_patches(
        originals,
        exact_patches,
        mismatch_message=lambda patch, count: (
            f"expected {patch.expected_count} Unsloth anchor(s) in "
            f"{patch.path}, found {count}"
        ),
    )


def _write_replacements(source_root: Path, replacements: dict[Path, str]) -> None:
    for relative_path, source in replacements.items():
        (source_root / relative_path).write_text(source, encoding="utf-8")


def patch_tree(source_root: Path) -> None:
    """Patch all desktop ownership surfaces after every upstream anchor validates."""
    module_path = _validate_module_path(source_root)
    patched = _validated_replacements(source_root, _PATCHES)

    _write_replacements(source_root, patched)
    module_path.write_text(_NIX_MODULE_SOURCE, encoding="utf-8")


def patch_backend_tree(backend_root: Path) -> None:
    """Patch every audited backend mutation surface after all anchors validate."""
    patched = _validated_replacements(backend_root, _BACKEND_PATCHES)
    _write_replacements(backend_root, patched)


def patch_all(source_root: Path, backend_root: Path) -> None:
    """Validate both source payloads before atomically beginning either patch pass."""
    module_path = _validate_module_path(source_root)
    desktop_patched = _validated_replacements(source_root, _PATCHES)
    backend_patched = _validated_replacements(backend_root, _BACKEND_PATCHES)

    _write_replacements(source_root, desktop_patched)
    module_path.write_text(_NIX_MODULE_SOURCE, encoding="utf-8")
    _write_replacements(backend_root, backend_patched)


def main(argv: list[str] | None = None) -> int:
    """Apply Nix ownership policy to one exact Unsloth source tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--backend-root", type=Path)
    parser.add_argument("--cargo-only", action="store_true")
    parser.add_argument("--desktop-version")
    args = parser.parse_args(argv)
    if args.cargo_only:
        if args.backend_root is not None:
            parser.error("--cargo-only cannot be combined with --backend-root")
        if args.desktop_version is None:
            parser.error("--cargo-only requires --desktop-version")
        patch_cargo_tree(args.source_root, args.desktop_version)
    elif args.desktop_version is not None:
        parser.error("--desktop-version requires --cargo-only")
    elif args.backend_root is None:
        patch_tree(args.source_root)
    else:
        patch_all(args.source_root, args.backend_root)
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
