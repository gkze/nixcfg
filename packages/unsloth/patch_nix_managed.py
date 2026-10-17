"""Apply the fail-closed Nix ownership policy to Unsloth Desktop source."""

from pathlib import Path
from typing import TYPE_CHECKING

from lib.codemods.source_patch import (
    SourcePatch,
    plan_patches,
    select_variant,
    source_tree_parser,
    write_planned,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_NIX_MODULE = Path("studio/src-tauri/src/nix_managed.rs")
_PATCH_SENTINEL = 'option_env!("UNSLOTH_NIX_BACKEND")'
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
    SourcePatch(
        "studio/src-tauri/tauri.conf.json",
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
    SourcePatch(
        "studio/src-tauri/tauri.macos.conf.json",
        """    "resources": {
      "../../install.sh": "install.sh"
    },
""",
        "",
    ),
    SourcePatch(
        "studio/src-tauri/src/main.rs",
        "mod native_path_policy;\nmod preflight;\n",
        "mod native_path_policy;\nmod nix_managed;\nmod preflight;\n",
    ),
    SourcePatch(
        "studio/src-tauri/src/main.rs",
        """fn set_launch_at_login(app: tauri::AppHandle, enabled: bool) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
""",
        """fn set_launch_at_login(app: tauri::AppHandle, enabled: bool) -> Result<bool, String> {
    crate::nix_managed::require_mutable("change launch-at-login")?;
    use tauri_plugin_autostart::ManagerExt;
""",
    ),
    SourcePatch(
        "studio/src-tauri/src/main.rs",
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
    SourcePatch(
        "studio/src-tauri/src/main.rs",
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
    SourcePatch(
        "studio/src-tauri/src/main.rs",
        "        .plugin(tauri_plugin_updater::Builder::new().build())\n",
        "        // Nix owns application updates; do not register the Tauri updater plugin.\n",
    ),
    SourcePatch(
        "studio/src-tauri/src/desktop_update_policy.rs",
        """const DESKTOP_UPDATER_MANIFEST_URL: &str =
    "https://github.com/unslothai/unsloth/releases/latest/download/latest.json";
""",
        """const DESKTOP_UPDATER_MANIFEST_URL: &str =
    "nix-managed://updates-disabled";
""",
    ),
    SourcePatch(
        "studio/src-tauri/src/desktop_update_policy.rs",
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
    SourcePatch(
        "studio/src-tauri/src/desktop_update_policy.rs",
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
    SourcePatch(
        "studio/src-tauri/src/desktop_update_policy.rs",
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
    SourcePatch(
        "studio/src-tauri/src/desktop_updater.rs",
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
    SourcePatch(
        "studio/src-tauri/src/commands.rs",
        """pub async fn start_install(
    app: AppHandle,
    state: tauri::State<'_, install::InstallState>,
    backend_state: tauri::State<'_, BackendState>,
    update_state: tauri::State<'_, update::UpdateState>,
    diagnostics: tauri::State<'_, DiagnosticsState>,
) -> Result<(), String> {
    if has_owned_backend(&backend_state)? {
""",
        """pub async fn start_install(
    app: AppHandle,
    state: tauri::State<'_, install::InstallState>,
    backend_state: tauri::State<'_, BackendState>,
    update_state: tauri::State<'_, update::UpdateState>,
    diagnostics: tauri::State<'_, DiagnosticsState>,
) -> Result<(), String> {
    crate::nix_managed::require_mutable("install the backend")?;
    if has_owned_backend(&backend_state)? {
""",
    ),
    SourcePatch(
        "studio/src-tauri/src/commands.rs",
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
    SourcePatch(
        "studio/src-tauri/src/commands.rs",
        """) -> Result<(), String> {
    let force_installer = force_installer.unwrap_or(false);
""",
        """) -> Result<(), String> {
    crate::nix_managed::require_mutable("repair the backend")?;
    let force_installer = force_installer.unwrap_or(false);
""",
    ),
    SourcePatch(
        "studio/src-tauri/src/install.rs",
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
    SourcePatch(
        "studio/src-tauri/src/update.rs",
        """) -> Result<(), String> {
    let attempt = match &kind {
""",
        """) -> Result<(), String> {
    crate::nix_managed::require_mutable("update, repair, or stage the backend")?;
    let attempt = match &kind {
""",
    ),
    SourcePatch(
        "studio/src-tauri/src/process.rs",
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
    SourcePatch(
        "studio/src-tauri/src/process.rs",
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
    SourcePatch(
        "studio/src-tauri/src/process.rs",
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
    SourcePatch(
        "studio/src-tauri/src/process.rs",
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
    SourcePatch(
        "studio/frontend/src/hooks/use-tauri-update.ts",
        'export type DesktopUpdatePolicyMode = "in_app" | "manual_linux_package";\n',
        """export type DesktopUpdatePolicyMode =
  | "in_app"
  | "manual_linux_package"
  | "nix_managed";
""",
    ),
    SourcePatch(
        "studio/frontend/src/hooks/use-tauri-update.ts",
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
    SourcePatch(
        "studio/frontend/src/hooks/use-tauri-update.ts",
        """      const { policy } = await resolveUpdatePolicy();
      if (policy.mode === "manual_linux_package") {
""",
        """      const { policy } = await resolveUpdatePolicy();
      if (policy.mode === "nix_managed") return;
      if (policy.mode === "manual_linux_package") {
""",
    ),
    SourcePatch(
        "studio/src-tauri/tauri.conf.json",
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
    SourcePatch(
        "studio/src-tauri/tauri.conf.json",
        '    "createUpdaterArtifacts": true,\n',
        '    "createUpdaterArtifacts": false,\n',
    ),
    SourcePatch(
        "studio/src-tauri/capabilities/default.json",
        '    "updater:default",\n',
        "",
    ),
)


_MANAGED_ERROR = "this Unsloth installation is managed by Nix"

# The 0.1.808-beta install() docstring is one upstream line long enough that the
# anchor text must be assembled from adjacent literals to stay under line lint.
_SD_CPP_INSTALL_DOCSTRING = (
    '    """Download and extract the prebuilt for this host, returning the sd-cli path. '
    "Resolves against the Unsloth mirror (``DEFAULT_REPO``) first; if the mirror cannot "
    "serve this host AND the default repo is in use, falls back to leejet upstream so "
    "native install still works. Raises ``RuntimeError`` only when neither source has an "
    'asset for the host, or the archive has no ``sd-cli``."""\n'
)

# Same lint constraint for the reflowed 2026.9.4 worker/ssm docstrings: the
# upstream text keeps its exact line breaks, so over-long lines are joined from
# adjacent fragments with no newline between them.
_WHEEL_FIRST_DOC = (
    '    \"\"\"Install a fast-path package, wheel first, and never leave an unusable one behind. The two\n'
    '    \"touch nothing\" guards run here, outside the cleanup: an already-working package returns\n'
    "    before any subprocess, and offline changes nothing. Everything after them is an install\n"
    "    attempt, so ANY unsuccessful exit -- timeout, failed install, bad import -- discards what is\n"
    "    left rather than leaving metadata the in-process import would pick up. Enforced here rather\n"
    '    than at each return because four separate exits have now been found that forgot to clean up.\"\"\"\n'
)
_SSM_KERNEL_DOC = (
    '    \"\"\"Install one kernel wheel-first, then a HIP-aware PyPI source build. Returns True iff '
    'importable afterwards; idempotent (no-op when already installed).\"\"\"\n'
)
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

# Studio's in-venv test has two supported upstream shapes: the released
# ``sys.prefix`` comparison and the ``_running_in_studio_venv`` helper that
# replaces it upstream. They are complete, mutually exclusive backend layouts;
# a partially migrated CLI matches neither and fails closed.
_STUDIO_VENV_SYS_PREFIX = SourcePatch(
    "unsloth_cli/commands/studio.py",
    """    studio_venv_dir = STUDIO_HOME / "unsloth_studio"
    in_studio_venv = sys.prefix.startswith(str(studio_venv_dir))
""",
    """    studio_venv_dir = STUDIO_HOME / "unsloth_studio"
    in_studio_venv = (
        os.environ.get("UNSLOTH_NIX_MANAGED") == "1"
        or sys.prefix.startswith(str(studio_venv_dir))
    )
""",
    expected_count=2,
)
_STUDIO_VENV_HELPER = SourcePatch(
    "unsloth_cli/commands/studio.py",
    """    studio_venv_dir = STUDIO_HOME / "unsloth_studio"
    in_studio_venv = _running_in_studio_venv(studio_venv_dir)
""",
    """    studio_venv_dir = STUDIO_HOME / "unsloth_studio"
    in_studio_venv = (
        os.environ.get("UNSLOTH_NIX_MANAGED") == "1"
        or _running_in_studio_venv(studio_venv_dir)
    )
""",
    expected_count=2,
)

_BACKEND_PATCHES = (
    SourcePatch(
        "pyproject.toml",
        """    "backend/core/data_recipe/oxc-validator/*.json",
    "backend/core/data_recipe/oxc-validator/*.mjs",
""",
        """    "backend/core/data_recipe/oxc-validator/*.json",
    "backend/core/data_recipe/oxc-validator/*.mjs",
    "backend/core/data_recipe/oxc-validator/node_modules/**/*",
""",
    ),
    SourcePatch(
        OXC_VALIDATOR_PATH.as_posix(),
        OXC_VALIDATOR_PATCH_SEAM,
        OXC_VALIDATOR_PATCH_REPLACEMENT,
    ),
    SourcePatch(
        "unsloth/chat_templates.py",
        '        os.system("pip -qqq install git+https://github.com/lm-sys/FastChat.git")\n',
        f'        raise RuntimeError("Cannot install FastChat: {_MANAGED_ERROR}.")\n',
        expected_count=2,
    ),
    SourcePatch(
        "unsloth/save.py",
        """def install_llama_cpp_clone_non_blocking():
    full_command = [
""",
        f"""def install_llama_cpp_clone_non_blocking():
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install llama.cpp at runtime: {_MANAGED_ERROR}.")
    full_command = [
""",
    ),
    SourcePatch(
        "unsloth/save.py",
        """def install_llama_cpp_make_non_blocking():
""",
        f"""def install_llama_cpp_make_non_blocking():
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot build llama.cpp at runtime: {_MANAGED_ERROR}.")
""",
    ),
    SourcePatch(
        "unsloth/save.py",
        """def install_python_non_blocking(packages = []):
    full_command = ["pip", "install"] + packages
""",
        f"""def install_python_non_blocking(packages = []):
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install Python packages at runtime: {_MANAGED_ERROR}.")
    full_command = ["pip", "install"] + packages
""",
    ),
    SourcePatch(
        "unsloth/save.py",
        """def install_llama_cpp_old(version = -10):
""",
        f"""def install_llama_cpp_old(version = -10):
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install llama.cpp at runtime: {_MANAGED_ERROR}.")
""",
    ),
    SourcePatch(
        "unsloth/save.py",
        """def install_llama_cpp_blocking(use_cuda = False):
""",
        f"""def install_llama_cpp_blocking(use_cuda = False):
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install llama.cpp at runtime: {_MANAGED_ERROR}.")
""",
    ),
    SourcePatch(
        "studio/install_python_stack.py",
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
    SourcePatch(
        "studio/install_sd_cpp_prebuilt.py",
        f"""def install(
    *,
    install_dir: Optional[Path] = None,
    accelerator: str = "auto",
    token: Optional[str] = None,
) -> Path:
{_SD_CPP_INSTALL_DOCSTRING}    target = install_dir or default_install_dir()
""",
        f"""def install(
    *,
    install_dir: Optional[Path] = None,
    accelerator: str = "auto",
    token: Optional[str] = None,
) -> Path:
{_SD_CPP_INSTALL_DOCSTRING}    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        raise RuntimeError("Cannot install package-owned stable-diffusion.cpp: {_MANAGED_ERROR}.")
    target = install_dir or default_install_dir()
""",
    ),
    SourcePatch(
        "studio/install_node_prebuilt.py",
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
    SourcePatch(
        "studio/install_llama_prebuilt.py",
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
    SourcePatch(
        "studio/install_whisper_prebuilt.py",
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
    SourcePatch(
        "unsloth_cli/commands/studio.py",
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
    _STUDIO_VENV_SYS_PREFIX,
    SourcePatch(
        "unsloth_cli/commands/studio.py",
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
    SourcePatch(
        "unsloth_cli/commands/studio.py",
        """    \"\"\"Update Unsloth Studio dependencies and rebuild.\"\"\"
""",
        f"""    \"\"\"Update Unsloth Studio dependencies and rebuild.\"\"\"
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        typer.echo("Cannot update Studio dependencies: {_MANAGED_ERROR}.", err = True)
        raise typer.Exit(1)
""",
    ),
    SourcePatch(
        "studio/backend/core/training/worker.py",
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
    SourcePatch(
        "studio/backend/core/training/worker.py",
        f"""def _install_package_wheel_first(
    *, event_queue: Any, import_name: str, display_name: str, pypi_name: str, **kwargs: Any
) -> bool:
{_WHEEL_FIRST_DOC}    if _is_importable(import_name):
        logger.info("%s already installed", display_name)
        return True

    if _model_offline_mode_enabled():
""",
        f"""def _install_package_wheel_first(
    *, event_queue: Any, import_name: str, display_name: str, pypi_name: str, **kwargs: Any
) -> bool:
{_WHEEL_FIRST_DOC}    if _is_importable(import_name):
        logger.info("%s already installed", display_name)
        return True

    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.info("Skipping %s installation in the Nix-managed backend", display_name)
        return False

    if _model_offline_mode_enabled():
""",
    ),
    SourcePatch(
        "studio/backend/core/training/worker.py",
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
    SourcePatch(
        "studio/backend/utils/transformers_version.py",
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
    SourcePatch(
        "studio/backend/utils/transformers_version.py",
        """def _ensure_venv_dir(venv_dir: str, packages: tuple[str, ...], label: str) -> bool:
    \"\"\"Ensure *venv_dir* exists with all *packages*. Install if missing.\"\"\"
    if _venv_dir_is_valid_and_undamaged(venv_dir, packages):
        # A live tree with content makes retired copies leftovers to sweep.
        _recover_retired_sidecar(venv_dir)
        return _top_up_optional_packages(venv_dir, packages)
""",
        """def _ensure_venv_dir(venv_dir: str, packages: tuple[str, ...], label: str) -> bool:
    \"\"\"Ensure *venv_dir* exists with all *packages*. Install if missing.\"\"\"
    if _venv_dir_is_valid_and_undamaged(venv_dir, packages):
        # A live tree with content makes retired copies leftovers to sweep.
        _recover_retired_sidecar(venv_dir)
        return _top_up_optional_packages(venv_dir, packages)
    if os.environ.get(\"UNSLOTH_NIX_MANAGED\") == \"1\":
        logger.warning(\"%s is absent from the Nix closure; runtime repair is disabled\", label)
        return False
""",
    ),
    SourcePatch(
        "studio/backend/utils/wheel_utils.py",
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
    SourcePatch(
        "studio/backend/utils/ssm_runtime.py",
        f"""def _install_kernel(
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
{_SSM_KERNEL_DOC}    if _is_importable(import_name):
        logger.info("%s already installed", display_name)
        return True

    from utils.utils import hf_env_offline
""",
        f"""def _install_kernel(
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
{_SSM_KERNEL_DOC}    if _is_importable(import_name):
        logger.info("%s already installed", display_name)
        return True
    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        logger.info("Skipping %s installation in the Nix-managed backend", display_name)
        return False

    from utils.utils import hf_env_offline
""",
    ),
    SourcePatch(
        "studio/backend/utils/ssm_runtime.py",
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
    SourcePatch(
        "studio/backend/utils/whisper_cpp_update.py",
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
    SourcePatch(
        "studio/backend/cloudflare_tunnel.py",
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
    SourcePatch(
        "studio/backend/utils/llama_cpp_update.py",
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
    SourcePatch(
        "studio/backend/core/inference/sd_cpp_backend.py",
        """    if not allow_install:
        return found
    with _install_lock:
""",
        """    if os.environ.get("UNSLOTH_NIX_MANAGED") == "1":
        return found if usable else None
    if not allow_install:
        return found
    with _install_lock:
""",
        expected_count=2,
    ),
    SourcePatch(
        "studio/setup.sh",
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
    SourcePatch(
        "studio/setup.sh",
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
    SourcePatch(
        "studio/setup.ps1",
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
    SourcePatch(
        "studio/setup.ps1",
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


_BACKEND_LAYOUTS = {
    "studio-venv-sys-prefix": _BACKEND_PATCHES,
    "studio-venv-helper": tuple(
        _STUDIO_VENV_HELPER if patch is _STUDIO_VENV_SYS_PREFIX else patch
        for patch in _BACKEND_PATCHES
    ),
}


def _plan_desktop(source_root: Path) -> dict[Path, str]:
    module_path = _validate_module_path(source_root)
    planned = plan_patches(source_root, _PATCHES)
    planned[module_path] = _NIX_MODULE_SOURCE
    return planned


def _plan_backend(backend_root: Path) -> dict[Path, str]:
    _layout, patches = select_variant(backend_root, _BACKEND_LAYOUTS)
    return plan_patches(backend_root, patches)


def patch_tree(source_root: Path) -> None:
    """Patch all desktop ownership surfaces after every upstream anchor validates."""
    write_planned(_plan_desktop(source_root))


def patch_backend_tree(backend_root: Path) -> None:
    """Patch every audited backend mutation surface after all anchors validate."""
    write_planned(_plan_backend(backend_root))


def patch_all(source_root: Path, backend_root: Path) -> None:
    """Validate both source payloads before atomically beginning either patch pass."""
    desktop = _plan_desktop(source_root)
    backend = _plan_backend(backend_root)
    write_planned(desktop)
    write_planned(backend)


def main(argv: Sequence[str] | None = None) -> int:
    """Apply Nix ownership policy to one exact Unsloth source tree."""
    parser = source_tree_parser(__doc__)
    parser.add_argument("--backend-root", type=Path)
    args = parser.parse_args(argv)
    if args.backend_root is None:
        patch_tree(args.source_root)
    else:
        patch_all(args.source_root, args.backend_root)
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
