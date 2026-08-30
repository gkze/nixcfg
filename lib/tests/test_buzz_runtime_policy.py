"""Behavioral and semantic contracts for Buzz's native-runtime policy patch."""

from functools import cache
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._buzz_native_lock import buzz_native_lock_string
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._updater_helpers import load_repo_module
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import Callable

    from nix_manipulator.expressions.scope import Scope

_PACKAGE_DIR = REPO_ROOT / "packages/buzz"
_PACKAGE_PATH = _PACKAGE_DIR / "package.nix"
_PATCHER_PATH = "packages/buzz/native/patch_runtime_policy.py"
_BUZZ_APP = Path("desktop/src-tauri/src/lib.rs")
_BUZZ_APP_STATE = Path("desktop/src-tauri/src/app_state.rs")
_BUZZ_APP_STATE_TESTS = Path("desktop/src-tauri/src/app_state_tests.rs")
_BUZZ_ENTRYPOINT = Path("desktop/src-tauri/src/mesh_llm/mod.rs")
_BUZZ_IDENTITY_COMMANDS = Path("desktop/src-tauri/src/commands/identity.rs")
_BUZZ_SECRET_STORE = Path("desktop/src-tauri/src/secret_store.rs")
_BUZZ_TAURI_IDENTITY = Path("desktop/src/shared/api/tauriIdentity.ts")
_BUZZ_KEYRING_LOCKED_SCREEN = Path(
    "desktop/src/features/onboarding/ui/KeyringLockedScreen.tsx"
)
_BUZZ_E2E_BRIDGE = Path("desktop/src/testing/e2eBridge.ts")
_BUZZ_IDENTITY_E2E = Path("desktop/tests/e2e/identity-lost.spec.ts")
_MESH_VERSION = buzz_native_lock_string("meshLlm", "version")
_SHERPA_VERSION = buzz_native_lock_string("sherpaOnnx", "version")
_MESH_RUNTIME_INSTALL = Path(f"mesh-llm-runtime-install-{_MESH_VERSION}/src/lib.rs")
_SHERPA_ONNX_SYS_BUILD = Path(f"sherpa-onnx-sys-{_SHERPA_VERSION}/build.rs")

_BUZZ_UPSTREAM = """use std::sync::Arc;

async fn initialize_mesh_native_runtime() -> anyhow::Result<()> {
    // The dynamic host runtime installs the recommended signed runtime on first
    // use when no compatible version is cached. Keep that SDK-owned path intact
    // so release builds work on clean machines without bundling llama.cpp or
    // requiring a separate `mesh-llm runtime install` command.
    mesh_llm_host_runtime::initialize_host_runtime()
        .await
        .map_err(|error| {
            anyhow::anyhow!("mesh native runtime failed to install or load: {error:#}")
        })
}
"""
_BUZZ_PATCHED = """use std::sync::Arc;

fn require_absolute_runtime_environment(
    variable: &str,
) -> anyhow::Result<()> {
    let value = std::env::var(variable)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            anyhow::anyhow!("{variable} must be set to a nonblank absolute path")
        })?;
    anyhow::ensure!(
        std::path::Path::new(&value).is_absolute(),
        "{variable} must be set to a nonblank absolute path"
    );
    Ok(())
}

fn ensure_nix_native_runtime_policy() -> anyhow::Result<()> {
    require_absolute_runtime_environment("MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR")?;
    require_absolute_runtime_environment("MESH_LLM_NATIVE_RUNTIME_CACHE_DIR")?;
    let manifest_url = std::env::var("MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL")
        .ok()
        .filter(|value| !value.trim().is_empty());
    anyhow::ensure!(
        manifest_url.is_none(),
        "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL must be unset or blank"
    );
    Ok(())
}

async fn initialize_mesh_native_runtime() -> anyhow::Result<()> {
    ensure_nix_native_runtime_policy()?;
    // The dynamic host runtime installs the recommended signed runtime on first
    // use when no compatible version is cached. Keep that SDK-owned path intact
    // so release builds work on clean machines without bundling llama.cpp or
    // requiring a separate `mesh-llm runtime install` command.
    mesh_llm_host_runtime::initialize_host_runtime()
        .await
        .map_err(|error| {
            anyhow::anyhow!("mesh native runtime failed to install or load: {error:#}")
        })
}
"""

_BUZZ_APP_UPSTREAM = """#![recursion_limit = "256"] // Deep Tauri command futures exceed the default layout query depth.
use std::sync::{atomic::AtomicBool, atomic::Ordering, Arc};
#[cfg(target_os = "macos")]
use tauri::Listener;

pub fn run() {
    let builder = tauri::Builder::default()
        .setup(move |app| {
            let app_handle = app.handle().clone();
            #[cfg(target_os = "macos")]
            {
                tray_menu::init(&app_handle)?;
            }

            let state = app_handle.state::<AppState>();
            resolve_persisted_identity(&app_handle, &state)?;
            if let Err(e) = backfill_persona_snapshots(&app_handle) {
                eprintln!("buzz-desktop: persona-snapshot backfill failed: {e}");
            }
            try_regenerate_nest(&app_handle);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_identity,
            get_nsec,
        ]);
}
"""

_BUZZ_APP_PRE_UI_KEYCHAIN_GUARDED = """#![recursion_limit = "256"] // Deep Tauri command futures exceed the default layout query depth.
#[cfg(all(feature = "system-keyring", target_os = "macos"))]
use security_framework::os::macos::keychain::SecKeychain;
use std::sync::{atomic::AtomicBool, atomic::Ordering, Arc};
#[cfg(target_os = "macos")]
use tauri::Listener;

pub fn run() {
    let builder = tauri::Builder::default()
        .setup(move |app| {
            let app_handle = app.handle().clone();
            // Keychain ACL dialogs can block Tauri setup before the first window
            // renders (notably after a signing-identity change). Disable prompts
            // for the complete setup phase so every pre-UI secret read fails
            // closed. Dropping this RAII guard at setup completion restores
            // interaction for explicit user-triggered recovery actions.
            #[cfg(all(feature = "system-keyring", target_os = "macos"))]
            let _pre_ui_keychain_interaction_lock =
                match SecKeychain::disable_user_interaction() {
                    Ok(lock) => lock,
                    Err(error) => {
                        eprintln!(
                            "buzz-desktop: could not disable pre-UI Keychain interaction: {error}"
                        );
                        let state = app_handle.state::<AppState>();
                        state.keyring_locked.store(true, Ordering::Release);
                        return Ok(());
                    }
                };
            #[cfg(target_os = "macos")]
            {
                tray_menu::init(&app_handle)?;
            }

            let state = app_handle.state::<AppState>();
            resolve_persisted_identity(&app_handle, &state)?;
            if !recovery_mode {
                if let Err(e) = backfill_persona_snapshots(&app_handle) {
                    eprintln!("buzz-desktop: persona-snapshot backfill failed: {e}");
                }
            }
            if !recovery_mode {
                try_regenerate_nest(&app_handle);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_identity,
            retry_keyring_identity,
            get_nsec,
        ]);
}
"""

_BUZZ_IDENTITY_IMPL_UPSTREAM = """impl IdentityKeyStore for crate::secret_store::SecretStore {
    fn probe(&self, name: &str) -> crate::secret_store::KeyringProbe {
        crate::secret_store::SecretStore::probe(self, name)
    }
    fn load(&self, name: &str) -> Result<Option<String>, String> {
        crate::secret_store::SecretStore::load(self, name)
    }
    fn store(&self, name: &str, value: &str) -> Result<(), String> {
        crate::secret_store::SecretStore::store(self, name, value)
    }
    fn delete(&self, name: &str) -> Result<(), String> {
        crate::secret_store::SecretStore::delete(self, name)
    }
    fn verify_stored(&self, key: &str, expected: &str) -> Result<bool, String> {
        crate::secret_store::SecretStore::verify_stored_raw(self, key, expected)
    }
}
"""
_BUZZ_IDENTITY_IMPL_READONLY = _BUZZ_IDENTITY_IMPL_UPSTREAM.replace(
    """    fn load(&self, name: &str) -> Result<Option<String>, String> {
        crate::secret_store::SecretStore::load(self, name)
    }
""",
    """    fn load(&self, name: &str) -> Result<Option<String>, String> {
        // Identity resolution runs during Tauri setup while macOS Keychain
        // interaction is suppressed. Keep it read-only so an authorized
        // legacy item survives the next guarded launch without a migration
        // write or delete prompt.
        crate::secret_store::SecretStore::load_readonly(self, name)
    }
""",
)
_BUZZ_IDENTITY_RETRY_HELPER = """

/// Retry a locked identity after the recovery UI is visible. This path is
/// deliberately read-only: it may prompt for access to the existing keyring
/// item, but it never stores, deletes, imports, or generates an identity.
pub(crate) fn retry_locked_identity(state: &AppState) -> Result<(), String> {
    let store = crate::secret_store::SecretStore::shared(keyring_service());
    retry_locked_identity_with_store(state, store)
}

fn retry_locked_identity_with_store(
    state: &AppState,
    store: &impl IdentityKeyStore,
) -> Result<(), String> {
    let _mutation_guard = state.identity_mutation.lock().map_err(|e| e.to_string())?;
    if !state
        .keyring_locked
        .load(std::sync::atomic::Ordering::Acquire)
    {
        return Err("identity is not waiting for keyring access".to_string());
    }

    let nsec = store
        .load(IDENTITY_KEY_NAME)
        .map_err(|error| format!("system keyring is still unavailable: {error}"))?
        .ok_or_else(|| {
            "identity is no longer present in the system keyring; re-import is required"
                .to_string()
        })?;
    let _keys = Keys::parse(nsec.trim())
        .map_err(|error| format!("existing keyring identity is invalid: {error}"))?;
    Ok(())
}
"""
_BUZZ_APP_STATE_UPSTREAM = _BUZZ_IDENTITY_IMPL_UPSTREAM
_BUZZ_APP_STATE_PATCHED = _BUZZ_IDENTITY_IMPL_READONLY + _BUZZ_IDENTITY_RETRY_HELPER

_BUZZ_IDENTITY_COMMANDS_UPSTREAM = """#[tauri::command]
pub fn get_default_relay_url() -> String {
    relay::relay_ws_url()
}
"""
_BUZZ_IDENTITY_COMMANDS_PATCHED = """#[tauri::command]
pub async fn retry_keyring_identity(app_handle: tauri::AppHandle) -> Result<(), String> {
    tokio::task::spawn_blocking(move || {
        let state = app_handle.state::<AppState>();
        crate::app_state::retry_locked_identity(&state)
    })
    .await
    .map_err(|error| format!("spawn_blocking failed: {error}"))?
}

#[tauri::command]
pub fn get_default_relay_url() -> String {
    relay::relay_ws_url()
}
"""

_BUZZ_TAURI_IDENTITY_UPSTREAM = """export async function getNsec(): Promise<string> {
  return invokeTauri<string>("get_nsec");
}
"""
_BUZZ_TAURI_IDENTITY_PATCHED = (
    _BUZZ_TAURI_IDENTITY_UPSTREAM
    + """
/** Retry access to the existing system-keyring identity after the UI is visible. */
export async function retryKeyringIdentity(): Promise<void> {
  await invokeTauri("retry_keyring_identity");
}
"""
)

_BUZZ_KEYRING_LOCKED_UPSTREAM = """import * as React from "react";
import { relaunch } from "@tauri-apps/plugin-process";
import { importIdentity } from "@/shared/api/tauriIdentity";

export function KeyringLockedScreen() {
  const [showImport, setShowImport] = React.useState(false);

  const handleReimportClick = React.useCallback(() => {
    setShowImport(true);
  }, []);

  return (
    <div>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          Your identity is safe in the OS keyring, but it's unreachable this
          session. Unlock your keyring or sign into your desktop session, then
          relaunch Buzz.
        </p>
          <div>
            <Button
              className="h-10 w-full"
              data-testid="relaunch-app"
              onClick={() => {
                void relaunch();
              }}
              type="button"
            >
              Relaunch Buzz
            </Button>
          </div>
    </div>
  );
}
"""
_BUZZ_KEYRING_LOCKED_PATCHED = """import * as React from "react";
import { relaunch } from "@tauri-apps/plugin-process";
import {
  importIdentity,
  retryKeyringIdentity,
} from "@/shared/api/tauriIdentity";

export function KeyringLockedScreen() {
  const [showImport, setShowImport] = React.useState(false);
  const [isRetrying, setIsRetrying] = React.useState(false);
  const [retryError, setRetryError] = React.useState<string | null>(null);

  const handleRetry = React.useCallback(async () => {
    setIsRetrying(true);
    setRetryError(null);
    try {
      await retryKeyringIdentity();
      // Setup deliberately skipped identity-dependent restoration while the
      // keyring was locked. Start a complete process only after the existing
      // key has been authorized and loaded successfully.
      await relaunch();
    } catch (error) {
      setRetryError(
        error instanceof Error ? error.message : String(error),
      );
      setIsRetrying(false);
    }
  }, []);

  const handleReimportClick = React.useCallback(() => {
    setShowImport(true);
  }, []);

  return (
    <div>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          Your identity is safe in the OS keyring, but Buzz could not access it
          before this window opened. Retry now so macOS can ask you to authorize
          this copy of Buzz, then Buzz will relaunch automatically.
        </p>
          <div>
            <Button
              className="h-10 w-full"
              data-testid="retry-keyring-access"
              disabled={isRetrying}
              onClick={() => {
                void handleRetry();
              }}
              type="button"
            >
              {isRetrying ? "Waiting for macOS…" : "Retry keyring access"}
            </Button>
            {retryError ? (
              <p className="text-sm text-destructive" role="alert">
                {retryError}
              </p>
            ) : null}
          </div>
    </div>
  );
}
"""

_BUZZ_E2E_BRIDGE_UPSTREAM = """async function handleMockCommand(command: string) {
  switch (command) {
      case "get_identity": {
        return { locked: true };
      }
  }
}
"""
_BUZZ_E2E_BRIDGE_PATCHED = """async function handleMockCommand(command: string) {
  switch (command) {
      case "retry_keyring_identity":
        return;
      case "get_identity": {
        return { locked: true };
      }
  }
}
"""

_BUZZ_IDENTITY_E2E_UPSTREAM = """test("locked screen relaunch button records the process-restart invoke", async ({
  page,
}) => {
  await installMockBridge(
    page,
    { identityLocked: true },
    { skipOnboardingSeed: true },
  );
  await page.goto("/");

  await expect(page.getByTestId("keyring-locked")).toBeVisible();
  await page.getByTestId("relaunch-app").click();

  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (
            window as Window & {
              __BUZZ_E2E_COMMAND_PAYLOADS__?: Array<{ command: string }>;
            }
          ).__BUZZ_E2E_COMMAND_PAYLOADS__?.some(
            (e) => e.command === "plugin:process|restart",
          ) ?? false,
      ),
    )
    .toBe(true);
});
"""
_BUZZ_IDENTITY_E2E_PATCHED = """test("locked screen retries the existing key and relaunches after success", async ({
  page,
}) => {
  await installMockBridge(
    page,
    { identityLocked: true },
    { skipOnboardingSeed: true },
  );
  await page.goto("/");

  await expect(page.getByTestId("keyring-locked")).toBeVisible();
  await page.getByTestId("retry-keyring-access").click();

  await expect
    .poll(() =>
      page.evaluate(() => {
        const commands = (
          window as Window & {
            __BUZZ_E2E_COMMAND_PAYLOADS__?: Array<{ command: string }>;
          }
        ).__BUZZ_E2E_COMMAND_PAYLOADS__?.map(({ command }) => command) ?? [];
        const retry = commands.indexOf("retry_keyring_identity");
        const restart = commands.indexOf("plugin:process|restart");
        return retry >= 0 && restart > retry;
      }),
    )
    .toBe(true);
});
"""

_KEYCHAIN_STORE_UPSTREAM = """#[cfg(all(feature = "system-keyring", target_os = "macos"))]
use security_framework::base::Error as SFError;
#[cfg(all(feature = "system-keyring", target_os = "macos"))]
use security_framework::passwords::{
    delete_generic_password_options, generic_password, PasswordOptions,
};

impl SecretStore {
    /// Probe whether `key` exists and whether the backend is reachable.
    pub fn probe(&self, key: &str) -> KeyringProbe {
        #[cfg(feature = "system-keyring")]
        {
            match self.load_blob() {
                Ok(Some(map)) => {
                    if map.contains_key(key) {
                        KeyringProbe::Present
                    } else {
                        // Blob exists but key absent — still check old per-key
                        // entries so a partial migration (e.g. identity migrated
                        // first) doesn't silently drop agent keys.
                        self.probe_legacy_key(key)
                    }
                }
                // No blob yet — check old per-key entries so callers that
                // gate `load()` on `Present` still trigger migration.
                Ok(None) => self.probe_legacy_key(key),
                Err(e) if is_keyring_availability_error(&e) => KeyringProbe::Unreachable,
                Err(_) => KeyringProbe::Unreachable, // corrupt blob — fail closed
            }
        }
        #[cfg(not(feature = "system-keyring"))]
        {
            let _ = key;
            KeyringProbe::Unreachable
        }
    }

    /// Check old per-key DPK/keyring entries for `key`. Used by `probe()` when
    /// the blob doesn't exist yet (first launch after upgrade).
    #[cfg(all(feature = "system-keyring", target_os = "macos"))]
    fn probe_legacy_key(&self, key: &str) -> KeyringProbe {
        match generic_password(dpk_opts(&self.service, key)) {
            Ok(_) => KeyringProbe::Present,
            Err(ref e) if is_not_found(e) => self.probe_legacy_key_keyring(key),
            Err(ref e) if is_dpk_unavailable(e) => self.probe_legacy_key_keyring(key),
            Err(ref e) if is_keyring_availability_error(&e.to_string()) => {
                KeyringProbe::Unreachable
            }
            Err(_) => KeyringProbe::ReachableButEmpty,
        }
    }

    #[cfg(feature = "system-keyring")]
    fn probe_legacy_key_keyring(&self, key: &str) -> KeyringProbe {
        match keyring_entry(&self.service, key) {
            Ok(entry) => match entry.get_password() {
                Ok(_) => KeyringProbe::Present,
                Err(keyring::Error::NoEntry) => KeyringProbe::ReachableButEmpty,
                Err(e) if is_keyring_availability_error(&e.to_string()) => {
                    KeyringProbe::Unreachable
                }
                Err(_) => KeyringProbe::ReachableButEmpty,
            },
            Err(e) if is_keyring_availability_error(&e.to_string()) => KeyringProbe::Unreachable,
            Err(_) => KeyringProbe::Unreachable,
        }
    }

    /// Read the secret for `key` without any legacy-migration side effects.
    ///
    /// Read the entire blob without any legacy-migration side effects.
    ///
    /// Returns the full key→value map when a blob exists, `Ok(None)` when no
    /// blob has been written yet, and `Err` only when the backend is
    /// unavailable. Never calls `migrate_legacy_key`.
    pub fn load_all_readonly(&self) -> Result<Option<HashMap<String, String>>, String> {
        #[cfg(feature = "system-keyring")]
        {
            self.load_blob()
        }
        #[cfg(not(feature = "system-keyring"))]
        {
            Err("system-keyring feature disabled".to_string())
        }
    }
}
"""

_KEYCHAIN_STORE_FAIL_CLOSED = """#[cfg(all(feature = "system-keyring", target_os = "macos"))]
use security_framework::base::Error as SFError;
#[cfg(all(feature = "system-keyring", target_os = "macos"))]
use security_framework::passwords::{
    delete_generic_password_options, generic_password, PasswordOptions,
};

impl SecretStore {
    /// Probe whether `key` exists and whether the backend is reachable.
    pub fn probe(&self, key: &str) -> KeyringProbe {
        #[cfg(feature = "system-keyring")]
        {
            match self.load_blob() {
                Ok(Some(map)) => {
                    if map.contains_key(key) {
                        KeyringProbe::Present
                    } else {
                        // Blob exists but key absent — still check old per-key
                        // entries so a partial migration (e.g. identity migrated
                        // first) doesn't silently drop agent keys.
                        self.probe_legacy_key(key)
                    }
                }
                // No blob yet — check old per-key entries so callers that
                // gate `load()` on `Present` still trigger migration.
                Ok(None) => self.probe_legacy_key(key),
                Err(e) if is_keyring_availability_error(&e) => KeyringProbe::Unreachable,
                Err(_) => KeyringProbe::Unreachable, // corrupt blob — fail closed
            }
        }
        #[cfg(not(feature = "system-keyring"))]
        {
            let _ = key;
            KeyringProbe::Unreachable
        }
    }

    /// Check old per-key DPK/keyring entries for `key`. Used by `probe()` when
    /// the blob doesn't exist yet (first launch after upgrade).
    #[cfg(all(feature = "system-keyring", target_os = "macos"))]
    fn probe_legacy_key(&self, key: &str) -> KeyringProbe {
        match generic_password(dpk_opts(&self.service, key)) {
            Ok(_) => KeyringProbe::Present,
            Err(ref e) if is_not_found(e) => self.probe_legacy_key_keyring(key),
            Err(ref e) if is_dpk_unavailable(e) => self.probe_legacy_key_keyring(key),
            Err(ref e) if is_keyring_availability_error(&e.to_string()) => {
                KeyringProbe::Unreachable
            }
            // Only an explicit item-not-found result proves the backend is
            // reachable and empty. Authorization and interaction failures must
            // preserve the existing identity by failing closed.
            Err(_) => KeyringProbe::Unreachable,
        }
    }

    #[cfg(feature = "system-keyring")]
    fn probe_legacy_key_keyring(&self, key: &str) -> KeyringProbe {
        match keyring_entry(&self.service, key) {
            Ok(entry) => match entry.get_password() {
                Ok(_) => KeyringProbe::Present,
                Err(keyring::Error::NoEntry) => KeyringProbe::ReachableButEmpty,
                Err(e) if is_keyring_availability_error(&e.to_string()) => {
                    KeyringProbe::Unreachable
                }
                // Prompt suppression reports ACL/authorization failures here.
                // They are not evidence that the entry is absent.
                Err(_) => KeyringProbe::Unreachable,
            },
            Err(e) if is_keyring_availability_error(&e.to_string()) => KeyringProbe::Unreachable,
            Err(_) => KeyringProbe::Unreachable,
        }
    }

    /// Read one secret without legacy-migration writes or deletes.
    ///
    /// The blob and every legacy macOS shape are read directly. Unlike
    /// [`Self::load`], this never calls `migrate_legacy_key`, `store`, or
    /// `delete`, so it is safe for an explicit ACL-authorization retry.
    pub fn load_readonly(&self, key: &str) -> Result<Option<String>, String> {
        #[cfg(feature = "system-keyring")]
        {
            #[cfg(target_os = "macos")]
            {
                return Self::resolve_readonly_lookup(
                    key,
                    || self.load_blob(),
                    || self.read_legacy_dpk_blob_readonly(),
                    || self.read_legacy_dpk_key_readonly(key),
                    || self.read_legacy_keyring_readonly(key),
                );
            }
            #[cfg(not(target_os = "macos"))]
            {
                return Self::resolve_readonly_lookup(
                    key,
                    || self.load_blob(),
                    || Ok(None),
                    || Ok(None),
                    || self.read_legacy_keyring_readonly(key),
                );
            }
        }
        #[cfg(not(feature = "system-keyring"))]
        {
            let _ = key;
            Err("system-keyring feature disabled".to_string())
        }
    }

    fn resolve_readonly_lookup<MainBlob, DpkBlob, DpkKey, LegacyKey>(
        key: &str,
        main_blob: MainBlob,
        dpk_blob: DpkBlob,
        dpk_key: DpkKey,
        legacy_key: LegacyKey,
    ) -> Result<Option<String>, String>
    where
        MainBlob: FnOnce() -> Result<Option<HashMap<String, String>>, String>,
        DpkBlob: FnOnce() -> Result<Option<HashMap<String, String>>, String>,
        DpkKey: FnOnce() -> Result<Option<String>, String>,
        LegacyKey: FnOnce() -> Result<Option<String>, String>,
    {
        if let Some(map) = main_blob()? {
            if let Some(value) = map.get(key) {
                return Ok(Some(value.clone()));
            }
        }
        let mut deferred_dpk_error = None;
        match dpk_blob() {
            Ok(Some(map)) => {
                if let Some(value) = map.get(key) {
                    return Ok(Some(value.clone()));
                }
            }
            Ok(None) => {}
            Err(error) => deferred_dpk_error = Some(error),
        }
        match dpk_key() {
            Ok(Some(value)) => return Ok(Some(value)),
            Ok(None) => {}
            Err(error) => {
                if deferred_dpk_error.is_none() {
                    deferred_dpk_error = Some(error);
                }
            }
        }
        if let Some(value) = legacy_key()? {
            return Ok(Some(value));
        }
        match deferred_dpk_error {
            Some(error) => Err(error),
            None => Ok(None),
        }
    }

    #[cfg(all(feature = "system-keyring", target_os = "macos"))]
    fn read_legacy_dpk_blob_readonly(
        &self,
    ) -> Result<Option<HashMap<String, String>>, String> {
        match generic_password(dpk_opts(&self.service, BLOB_KEY)) {
            Ok(bytes) => {
                let json = String::from_utf8(bytes)
                    .map_err(|error| format!("dpk blob utf8: {error}"))?;
                let map = serde_json::from_str::<HashMap<String, String>>(&json)
                    .map_err(|error| format!("dpk blob json: {error}"))?;
                Ok(Some(map))
            }
            Err(ref error) if is_not_found(error) => Ok(None),
            Err(error) => Err(format!("dpk blob read: {error}")),
        }
    }

    #[cfg(all(feature = "system-keyring", target_os = "macos"))]
    fn read_legacy_dpk_key_readonly(&self, key: &str) -> Result<Option<String>, String> {
        match generic_password(dpk_opts(&self.service, key)) {
            Ok(bytes) => String::from_utf8(bytes)
                .map(Some)
                .map_err(|error| format!("keyring utf8: {error}")),
            Err(ref error) if is_not_found(error) => Ok(None),
            Err(error) => Err(format!("keyring get: {error}")),
        }
    }

    #[cfg(feature = "system-keyring")]
    fn read_legacy_keyring_readonly(&self, key: &str) -> Result<Option<String>, String> {
        let entry =
            keyring_entry(&self.service, key).map_err(|error| format!("keyring entry: {error}"))?;
        match entry.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) if is_keyring_availability_error(&error.to_string()) => {
                Err(format!("keyring unavailable: {error}"))
            }
            Err(error) => Err(format!("keyring read: {error}")),
        }
    }

    /// Read the entire blob without any legacy-migration side effects.
    ///
    /// Returns the full key→value map when a blob exists, `Ok(None)` when no
    /// blob has been written yet, and `Err` only when the backend is
    /// unavailable. Never calls `migrate_legacy_key`.
    pub fn load_all_readonly(&self) -> Result<Option<HashMap<String, String>>, String> {
        #[cfg(feature = "system-keyring")]
        {
            self.load_blob()
        }
        #[cfg(not(feature = "system-keyring"))]
        {
            Err("system-keyring feature disabled".to_string())
        }
    }
}
"""

_DPK_PROBE_FAIL_CLOSED_EXPECTED = """    /// Check old per-key DPK/keyring entries for `key`. Used by `probe()` when
    /// the blob doesn't exist yet (first launch after upgrade).
    #[cfg(all(feature = "system-keyring", target_os = "macos"))]
    fn probe_legacy_key(&self, key: &str) -> KeyringProbe {
        match generic_password(dpk_opts(&self.service, key)) {
            Ok(_) => KeyringProbe::Present,
            Err(ref e) if is_not_found(e) => self.probe_legacy_key_keyring(key),
            Err(ref e) if is_dpk_unavailable(e) => self.probe_legacy_key_keyring(key),
            Err(ref e) if is_keyring_availability_error(&e.to_string()) => {
                KeyringProbe::Unreachable
            }
            // Only an explicit item-not-found result proves the backend is
            // reachable and empty. Authorization and interaction failures must
            // preserve the existing identity by failing closed.
            Err(_) => KeyringProbe::Unreachable,
        }
    }
"""
_DPK_PROBE_BLOB_AWARE_EXPECTED = """    /// Check every old DPK/keyring shape for `key` without migration.
    #[cfg(all(feature = "system-keyring", target_os = "macos"))]
    fn probe_legacy_key(&self, key: &str) -> KeyringProbe {
        Self::classify_legacy_dpk_blob_probe(
            key,
            self.read_legacy_dpk_blob_readonly(),
            || self.probe_legacy_dpk_key(key),
        )
    }

    fn classify_legacy_dpk_blob_probe<Fallback>(
        key: &str,
        dpk_blob: Result<Option<HashMap<String, String>>, String>,
        fallback: Fallback,
    ) -> KeyringProbe
    where
        Fallback: FnOnce() -> KeyringProbe,
    {
        match dpk_blob {
            Ok(Some(map)) if map.contains_key(key) => KeyringProbe::Present,
            Ok(_) => fallback(),
            Err(_) => Self::classify_inaccessible_dpk_fallback(fallback()),
        }
    }

    fn classify_inaccessible_dpk_fallback(fallback: KeyringProbe) -> KeyringProbe {
        match fallback {
            KeyringProbe::Present => KeyringProbe::Present,
            _ => KeyringProbe::Unreachable,
        }
    }

    #[cfg(all(feature = "system-keyring", target_os = "macos"))]
    fn probe_legacy_dpk_key(&self, key: &str) -> KeyringProbe {
        match generic_password(dpk_opts(&self.service, key)) {
            Ok(_) => KeyringProbe::Present,
            Err(ref e) if is_not_found(e) => self.probe_legacy_key_keyring(key),
            // Any other DPK failure leaves that storage shape unknown. A
            // concrete legacy value may still win, but an empty or failed
            // legacy fallback cannot prove the identity absent.
            Err(_) => Self::classify_inaccessible_dpk_fallback(
                self.probe_legacy_key_keyring(key),
            ),
        }
    }
"""
_KEYCHAIN_STORE_FAIL_CLOSED = _KEYCHAIN_STORE_FAIL_CLOSED.replace(
    _DPK_PROBE_FAIL_CLOSED_EXPECTED,
    _DPK_PROBE_BLOB_AWARE_EXPECTED,
)

_KEYRING_STORE_TESTS_UPSTREAM = """
#[cfg(all(test, feature = "system-keyring"))]
mod tests {
    use super::*;

    #[test]
    fn probe_returns_present_when_key_in_cache() {
    }
}
"""
_KEYCHAIN_STORE_UPSTREAM += _KEYRING_STORE_TESTS_UPSTREAM
_KEYCHAIN_STORE_FAIL_CLOSED += _KEYRING_STORE_TESTS_UPSTREAM

_MESH_UPSTREAM = """const MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL_ENV: &str =
    "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL";

impl Default for NativeRuntimeManifestOptions {
    fn default() -> Self {
        Self {
            mesh_version: CURRENT_MESH_VERSION.to_string(),
            manifest_path: None,
            manifest_url: None,
            bundle_dirs: Vec::new(),
            allow_default_manifest_url: true,
        }
    }
}

impl Default for NativeRuntimeInstallOptions {
    fn default() -> Self {
        Self {
            mesh_version: CURRENT_MESH_VERSION.to_string(),
            skippy_abi_version: None,
            selection: RuntimeSelection::Recommended,
            manifest_path: None,
            manifest_url: None,
            bundle_dirs: Vec::new(),
            cache_dir: None,
            verification_policy: NativeRuntimeVerificationPolicy::RequireChecksum,
            bundle_install_policy: NativeRuntimeBundleInstallPolicy::UseInPlace,
            progress: None,
            allow_download: true,
        }
    }
}

pub async fn install_native_runtime(
    options: NativeRuntimeInstallOptions,
) -> Result<NativeRuntimeInstallOutcome> {
    let (manifest, bundle_dirs) =
        load_release_manifest_with_bundle_dirs(NativeRuntimeManifestOptions {
            mesh_version: options.mesh_version.clone(),
            manifest_path: options.manifest_path.clone(),
            manifest_url: options.manifest_url.clone(),
            bundle_dirs: options.bundle_dirs.clone(),
            allow_default_manifest_url: true,
        })
        .await?;
    if manifest.artifacts.is_empty() {
        bail!("no native runtime manifest entries found");
    }
    let skippy_abi_version = options
        .skippy_abi_version
        .clone()
        .unwrap_or_else(|| manifest.skippy_abi.clone());
    let cache = native_runtime_cache(options.cache_dir.as_deref())?;
    let resolution = NativeRuntimeResolver::new(
        &options.mesh_version,
        host_runtime_profile(),
        manifest,
        cache.clone(),
    )
    .with_skippy_abi_version(skippy_abi_version)
    .with_bundle_dirs(bundle_dirs)
    .resolve(&options.selection)?;
    install_resolved_runtime(&cache, resolution, &options).await
}

fn unrelated_policy_example() -> NativeRuntimeInstallOptions {
    NativeRuntimeInstallOptions {
        allow_download: true,
        ..Default::default()
    }
}
"""
_MESH_PATCHED = (
    _MESH_UPSTREAM
    .replace(
        "allow_default_manifest_url: true,",
        "allow_default_manifest_url: false,",
        1,
    )
    .replace(
        "allow_download: true,",
        "allow_download: false,",
        1,
    )
    .replace(
        "allow_default_manifest_url: true,",
        "allow_default_manifest_url: false,",
        1,
    )
)

_MANIFEST_DEFAULT_COMMENT_DECOY = """/*
impl Default for NativeRuntimeManifestOptions {
    fn default() -> Self {
        Self {
            allow_default_manifest_url: true,
        }
    }
}
*/
"""
_INSTALL_DEFAULT_COMMENT_DECOY = """/*
impl Default for NativeRuntimeInstallOptions {
    fn default() -> Self {
        Self {
            verification_policy: NativeRuntimeVerificationPolicy::RequireChecksum,
            allow_download: true,
        }
    }
}
*/
"""
_INSTALL_MANIFEST_COMMENT_DECOY = """/*
pub async fn install_native_runtime(
    options: NativeRuntimeInstallOptions,
) -> anyhow::Result<InstalledNativeRuntime> {
    let manifest = load_release_manifest_with_bundle_dirs(NativeRuntimeManifestOptions {
        allow_default_manifest_url: true,
    });
}
*/
"""

_SHERPA_STATIC_LIBS_UPSTREAM = """const SHERPA_ONNX_STATIC_LIBS: &[&str] = &[
    "sherpa-onnx-c-api",
    "sherpa-onnx-core",
    "kaldi-decoder-core",
    "sherpa-onnx-kaldifst-core",
    "sherpa-onnx-fstfar",
    "sherpa-onnx-fst",
    "kaldi-native-fbank-core",
    "kissfft-float",
    "piper_phonemize",
    "espeak-ng",
    "ucd",
    "onnxruntime",
    "ssentencepiece_core",
];
"""
_SHERPA_STATIC_LIBS_TTS_OFF = """const SHERPA_ONNX_STATIC_LIBS: &[&str] = &[
    "sherpa-onnx-c-api",
    "sherpa-onnx-core",
    "kaldi-decoder-core",
    "sherpa-onnx-kaldifst-core",
    "sherpa-onnx-fstfar",
    "sherpa-onnx-fst",
    "kaldi-native-fbank-core",
    "kissfft-float",
    "onnxruntime",
    "ssentencepiece_core",
];
"""
_SHERPA_BUILD_UPSTREAM = (
    "use std::env;\n\n"
    + _SHERPA_STATIC_LIBS_UPSTREAM
    + "\nfn emit_static_link_directives() {}\n"
)
_SHERPA_BUILD_TTS_OFF = _SHERPA_BUILD_UPSTREAM.replace(
    _SHERPA_STATIC_LIBS_UPSTREAM,
    _SHERPA_STATIC_LIBS_TTS_OFF,
)


@cache
def _patcher() -> ModuleType:
    return load_repo_module(_PATCHER_PATH, "buzz_runtime_policy_patch_test")


@cache
def _package_scope() -> Scope:
    package = expect_instance(
        parse_nix_expr(_PACKAGE_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    asserted = expect_instance(package.output, Assertion)
    return asserted.body.scope


def _write_buzz_source(root: Path, source: str = _BUZZ_UPSTREAM) -> Path:
    path = root / _BUZZ_ENTRYPOINT
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    _write_buzz_app(root)
    _write_source_file(root, _BUZZ_APP_STATE, _BUZZ_APP_STATE_UPSTREAM)
    _write_source_file(
        root,
        _BUZZ_APP_STATE_TESTS,
        "#[test]\nfn corrupt_keyring_recovers_valid_file_without_rotating() {\n}\n",
    )
    _write_source_file(
        root,
        _BUZZ_IDENTITY_COMMANDS,
        _BUZZ_IDENTITY_COMMANDS_UPSTREAM,
    )
    _write_buzz_secret_store(root)
    _write_source_file(root, _BUZZ_TAURI_IDENTITY, _BUZZ_TAURI_IDENTITY_UPSTREAM)
    _write_source_file(
        root,
        _BUZZ_KEYRING_LOCKED_SCREEN,
        _BUZZ_KEYRING_LOCKED_UPSTREAM,
    )
    _write_source_file(root, _BUZZ_E2E_BRIDGE, _BUZZ_E2E_BRIDGE_UPSTREAM)
    _write_source_file(root, _BUZZ_IDENTITY_E2E, _BUZZ_IDENTITY_E2E_UPSTREAM)
    return path


def _write_source_file(root: Path, relative_path: Path, source: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _write_buzz_app(root: Path, source: str = _BUZZ_APP_UPSTREAM) -> Path:
    path = root / _BUZZ_APP
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _write_buzz_secret_store(
    root: Path,
    source: str = _KEYCHAIN_STORE_UPSTREAM,
) -> Path:
    path = root / _BUZZ_SECRET_STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _write_mesh_vendor(
    root: Path,
    source: str = _MESH_UPSTREAM,
    *,
    prefix: str = "vendor",
) -> Path:
    path = root / prefix / _MESH_RUNTIME_INSTALL
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    return path


def _write_sherpa_vendor(
    root: Path,
    source: str = _SHERPA_BUILD_UPSTREAM,
    *,
    prefix: str = "vendor",
) -> Path:
    path = root / prefix / _SHERPA_ONNX_SYS_BUILD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("source", "valid"),
    [
        ("'", False),
        ("''", False),
        ("'a'", True),
        (r"'\n'", True),
        (r"'\x2f'", True),
        (r"'\u{2f}'", True),
        (r"'\u{2f'", False),
        ("'a", False),
        ("'\\", False),
    ],
)
def test_rust_character_literal_scanner_distinguishes_lifetimes_and_escapes(
    source: str,
    *,
    valid: bool,
) -> None:
    """Only complete Rust character literals may be masked as non-code."""
    end = _patcher()._rust_char_literal_end(source, 0)

    assert (end == len(source)) is valid


def test_rust_lexical_mask_preserves_offsets_while_hiding_non_code() -> None:
    """Comments and every reviewed Rust literal form cannot supply code tokens."""
    source = r"""pub fn active<'a>() {
    let slash = '/';
    let quote = '"';
    let escaped = '\n';
    let hex = '\x2f';
    let unicode = '\u{2f}';
    let normal = "/* hidden normal */ \"still a string\"";
    let raw = r##"impl Hidden { /* hidden raw */ }"##;
    let bytes = br#"fn hidden_bytes() {}"#;
    let c_string = cr"// hidden c string";
    // hidden line comment
    /* hidden outer
       /* hidden nested */
    */
    active();
}
"""

    masked = _patcher().mask_rust_non_code(source)

    assert len(masked) == len(source)
    assert masked.count("\n") == source.count("\n")
    assert "pub fn active<'a>()" in masked
    assert "active();" in masked
    assert "hidden" not in masked
    assert _patcher().mask_rust_non_code("code(); // hidden at eof") == (
        "code();                 "
    )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("/* unterminated", "unterminated block comment"),
        ('let value = r#"unterminated;', "unterminated raw string literal"),
        ('let value = "unterminated;', "unterminated string literal"),
    ],
)
def test_rust_lexical_mask_rejects_unterminated_non_code(
    source: str,
    message: str,
) -> None:
    """Malformed Rust cannot weaken policy matching through lexer recovery."""
    with pytest.raises(_patcher().RuntimePolicyPatchError, match=message):
        _patcher().mask_rust_non_code(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("impl Reviewed {}", False),
        ("#[cfg(any())]\nimpl Reviewed {}", True),
        ("#   [cfg(any())]\nimpl Reviewed {}", True),
        ('#[cfg(any(feature = ["reviewed"]))]\n\nimpl Reviewed {}', True),
        (
            "]\nimpl Reviewed {}",
            True,
        ),
        ("[not_an_attribute]\nimpl Reviewed {}", False),
    ],
    ids=[
        "none",
        "simple",
        "spaced-marker",
        "nested-brackets",
        "unbalanced",
        "missing-hash",
    ],
)
def test_rust_outer_attribute_detection_is_fail_closed(
    source: str,
    *,
    expected: bool,
) -> None:
    """Only a complete attribute-free reviewed item may be transformed."""
    start = source.index("impl Reviewed")

    assert _patcher().rust_item_has_outer_attribute(source, start) is expected


def test_reviewed_rust_item_replacement_requires_exact_raw_bytes() -> None:
    """Masked string contents cannot hide byte drift in a reviewed preimage."""
    before = 'impl Reviewed { const VALUE: &str = "good"; }'
    drifted = 'impl Reviewed { const VALUE: &str = "evil"; }'

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="reviewed item bytes drifted",
    ):
        _patcher()._replace_reviewed_rust_item(
            drifted,
            before,
            before.replace("good", "safe"),
            context="reviewed test item",
        )


@pytest.mark.parametrize(
    ("opening", "closing"),
    [("(", ")"), ("[", "]")],
    ids=["parentheses", "brackets"],
)
def test_reviewed_rust_item_replacement_rejects_macro_token_trees(
    opening: str,
    closing: str,
) -> None:
    """An exact item inside inactive macro tokens is not a root Rust item."""
    before = "pub fn reviewed() {}"
    source = (
        f"macro_rules! decoy {opening} () => {opening}\n{before}\n{closing}; {closing};"
    )

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="reviewed item is not an active root item",
    ):
        _patcher()._replace_reviewed_rust_item(
            source,
            before,
            "pub fn patched() {}",
            context="reviewed macro item",
        )


def test_buzz_source_patch_guards_host_initialization_directly(tmp_path: Path) -> None:
    """A copied Buzz tree must validate both paths and reject a manifest URL."""
    source_path = _write_buzz_source(tmp_path)

    _patcher().main(["buzz-source", str(tmp_path)])

    assert source_path.read_text(encoding="utf-8") == _BUZZ_PATCHED


def test_buzz_source_patch_makes_unexpected_keyring_probe_failures_unreachable(
    tmp_path: Path,
) -> None:
    """Only explicit item-not-found results may classify a Keychain as empty."""
    _write_buzz_source(tmp_path)
    secret_store_path = tmp_path / _BUZZ_SECRET_STORE

    _patcher().main(["buzz-source", str(tmp_path)])

    assert secret_store_path.read_text(encoding="utf-8") == (
        _KEYCHAIN_STORE_FAIL_CLOSED.replace(
            _patcher()._KEYRING_STORE_TEST_ANCHOR,
            _patcher()._KEYRING_STORE_READONLY_TESTS,
        )
    )


def test_buzz_source_patch_guards_every_pre_ui_keychain_load(tmp_path: Path) -> None:
    """The setup guard covers identity and skips owner-keyed recovery loads."""
    _write_buzz_source(tmp_path)
    app_path = tmp_path / _BUZZ_APP

    _patcher().main(["buzz-source", str(tmp_path)])

    assert app_path.read_text(encoding="utf-8") == (_BUZZ_APP_PRE_UI_KEYCHAIN_GUARDED)


def test_buzz_source_patch_adds_explicit_readonly_keyring_retry(
    tmp_path: Path,
) -> None:
    """Recovery retries the existing key after render, then forces a relaunch."""
    _write_buzz_source(tmp_path)

    _patcher().main(["buzz-source", str(tmp_path)])

    assert (tmp_path / _BUZZ_APP_STATE).read_text(encoding="utf-8") == (
        _BUZZ_APP_STATE_PATCHED
    )
    assert (tmp_path / _BUZZ_APP_STATE_TESTS).read_text(encoding="utf-8") == (
        f"{_patcher()._BUZZ_APP_STATE_RETRY_TESTS}}}\n"
    )
    assert (tmp_path / _BUZZ_IDENTITY_COMMANDS).read_text(encoding="utf-8") == (
        _BUZZ_IDENTITY_COMMANDS_PATCHED
    )
    assert (tmp_path / _BUZZ_TAURI_IDENTITY).read_text(encoding="utf-8") == (
        _BUZZ_TAURI_IDENTITY_PATCHED
    )
    assert (tmp_path / _BUZZ_KEYRING_LOCKED_SCREEN).read_text(
        encoding="utf-8"
    ) == _BUZZ_KEYRING_LOCKED_PATCHED
    assert (tmp_path / _BUZZ_E2E_BRIDGE).read_text(encoding="utf-8") == (
        _BUZZ_E2E_BRIDGE_PATCHED
    )
    assert (tmp_path / _BUZZ_IDENTITY_E2E).read_text(encoding="utf-8") == (
        _BUZZ_IDENTITY_E2E_PATCHED
    )


@pytest.mark.parametrize(
    "source",
    [
        _BUZZ_TAURI_IDENTITY_UPSTREAM.replace("getNsec", "getNsecDrifted", 1),
        _BUZZ_TAURI_IDENTITY_UPSTREAM * 2,
    ],
    ids=["missing", "duplicate"],
)
def test_buzz_source_patch_rejects_frontend_retry_span_drift_atomically(
    tmp_path: Path,
    source: str,
) -> None:
    """A missing or duplicated frontend anchor must leave every source untouched."""
    entrypoint = _write_buzz_source(tmp_path)
    tauri_identity = tmp_path / _BUZZ_TAURI_IDENTITY
    tauri_identity.write_text(source, encoding="utf-8")

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="Buzz Keychain retry frontend API: expected exactly 1 reviewed span",
    ):
        _patcher().patch_buzz_source(tmp_path)

    assert entrypoint.read_text(encoding="utf-8") == _BUZZ_UPSTREAM
    assert tauri_identity.read_text(encoding="utf-8") == source


def test_buzz_source_patch_rejects_recursion_attribute_drift_atomically(
    tmp_path: Path,
) -> None:
    """The exact root recursion attribute is required before any source is changed."""
    entrypoint = _write_buzz_source(tmp_path)
    app = tmp_path / _BUZZ_APP
    drifted = _BUZZ_APP_UPSTREAM.replace(
        '#![recursion_limit = "256"]',
        '#![recursion_limit = "512"]',
        1,
    )
    app.write_text(drifted, encoding="utf-8")

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="Buzz app recursion-limit attribute drifted",
    ):
        _patcher().patch_buzz_source(tmp_path)

    assert entrypoint.read_text(encoding="utf-8") == _BUZZ_UPSTREAM
    assert app.read_text(encoding="utf-8") == drifted


def test_buzz_source_patch_rejects_unbalanced_root_delimiters_atomically(
    tmp_path: Path,
) -> None:
    """Malformed root delimiters cannot make a reviewed Rust item look active."""
    entrypoint = _write_buzz_source(tmp_path)
    app = tmp_path / _BUZZ_APP
    attribute, source = _BUZZ_APP_UPSTREAM.split("\n", 1)
    drifted = f"{attribute}\n]\n{source}"
    app.write_text(drifted, encoding="utf-8")

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="Buzz pre-UI Keychain interaction import: reviewed item is not an active root item",
    ):
        _patcher().patch_buzz_source(tmp_path)

    assert entrypoint.read_text(encoding="utf-8") == _BUZZ_UPSTREAM
    assert app.read_text(encoding="utf-8") == drifted


@pytest.mark.parametrize(
    "source",
    [
        _BUZZ_UPSTREAM.replace(
            "mesh_llm_host_runtime::initialize_host_runtime()",
            "initialize_host_runtime_with_unreviewed_options()",
        ),
        _BUZZ_UPSTREAM + _BUZZ_UPSTREAM,
    ],
    ids=["missing", "duplicate"],
)
def test_buzz_source_patch_rejects_startup_anchor_drift(
    tmp_path: Path,
    source: str,
) -> None:
    """Missing or duplicated startup anchors must leave the source untouched."""
    source_path = _write_buzz_source(tmp_path, source)

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="Buzz Mesh runtime startup anchor",
    ):
        _patcher().patch_buzz_source(tmp_path)

    assert source_path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    "decoy",
    [
        lambda reviewed: f"\n/*\n{reviewed}\n*/\n",
        lambda reviewed: f'\nconst DECOY: &str = r###"{reviewed}"###;\n',
    ],
    ids=["comment", "raw-string"],
)
def test_buzz_source_patch_rejects_non_code_startup_decoys(
    tmp_path: Path,
    decoy: Callable[[str], str],
) -> None:
    """A reviewed startup function in non-code cannot mask active source drift."""
    reviewed_decoy = decoy(_BUZZ_UPSTREAM)
    source = _BUZZ_UPSTREAM.replace("async fn", "async  fn", 1) + reviewed_decoy
    source_path = _write_buzz_source(tmp_path, source)

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="Buzz Mesh runtime startup anchor",
    ):
        _patcher().patch_buzz_source(tmp_path)

    assert source_path.read_text(encoding="utf-8") == source


def test_desktop_vendor_patch_disables_mesh_network_and_sherpa_tts_links(
    tmp_path: Path,
) -> None:
    """The copied vendor must apply both independently reviewed runtime policies."""
    mesh_path = _write_mesh_vendor(tmp_path)
    sherpa_path = _write_sherpa_vendor(tmp_path)

    _patcher().main(["desktop-cargo-deps", str(tmp_path)])

    assert mesh_path.read_text(encoding="utf-8") == _MESH_PATCHED
    assert sherpa_path.read_text(encoding="utf-8") == _SHERPA_BUILD_TTS_OFF


def test_desktop_vendor_patch_requires_exactly_one_mesh_runtime_crate(
    tmp_path: Path,
) -> None:
    """A missing or duplicated updater-pinned Mesh crate must fail closed."""
    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="found 0",
    ):
        _patcher().patch_desktop_cargo_deps(tmp_path)

    _write_mesh_vendor(tmp_path, prefix="first")
    _write_mesh_vendor(tmp_path, prefix="second")
    _write_sherpa_vendor(tmp_path)
    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="found 2",
    ):
        _patcher().patch_desktop_cargo_deps(tmp_path)


def test_desktop_vendor_patch_requires_exactly_one_sherpa_sys_crate(
    tmp_path: Path,
) -> None:
    """A missing or duplicated updater-pinned Sherpa crate must fail closed."""
    mesh_path = _write_mesh_vendor(tmp_path)
    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match=rf"sherpa-onnx-sys-{_SHERPA_VERSION}/build\.rs.*found 0",
    ):
        _patcher().patch_desktop_cargo_deps(tmp_path)
    assert mesh_path.read_text(encoding="utf-8") == _MESH_UPSTREAM

    _write_sherpa_vendor(tmp_path, prefix="first")
    _write_sherpa_vendor(tmp_path, prefix="second")
    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match=rf"sherpa-onnx-sys-{_SHERPA_VERSION}/build\.rs.*found 2",
    ):
        _patcher().patch_desktop_cargo_deps(tmp_path)
    assert mesh_path.read_text(encoding="utf-8") == _MESH_UPSTREAM


def test_desktop_vendor_patch_rejects_reviewed_literal_drift(
    tmp_path: Path,
) -> None:
    """All three reviewed true literals are mandatory and patched atomically."""
    drifted = _MESH_UPSTREAM.replace(
        "allow_download: true,",
        "allow_download: false,",
        1,
    )
    source_path = _write_mesh_vendor(tmp_path, drifted)
    sherpa_path = _write_sherpa_vendor(tmp_path)

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="InstallOptions allow_download default.*found 0",
    ):
        _patcher().patch_desktop_cargo_deps(tmp_path)

    assert source_path.read_text(encoding="utf-8") == drifted
    assert sherpa_path.read_text(encoding="utf-8") == _SHERPA_BUILD_UPSTREAM


def test_desktop_vendor_patch_rejects_non_code_sherpa_list_decoy(
    tmp_path: Path,
) -> None:
    """A commented reviewed link list cannot mask the active static list."""
    source = (
        _SHERPA_BUILD_UPSTREAM.replace("const SHERPA", "const  SHERPA", 1)
        + "\n/*\n"
        + _SHERPA_STATIC_LIBS_UPSTREAM
        + "\n*/\n"
    )
    mesh_path = _write_mesh_vendor(tmp_path)
    sherpa_path = _write_sherpa_vendor(tmp_path, source)

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="sherpa-onnx-sys static link list",
    ):
        _patcher().patch_desktop_cargo_deps(tmp_path)

    assert mesh_path.read_text(encoding="utf-8") == _MESH_UPSTREAM
    assert sherpa_path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    ("source", "context"),
    [
        (
            _MESH_UPSTREAM.replace(
                "allow_default_manifest_url: true,",
                "allow_default_manifest_url: bool::from(true),",
                1,
            )
            + _MANIFEST_DEFAULT_COMMENT_DECOY,
            "ManifestOptions allow_default_manifest_url default",
        ),
        (
            _MESH_UPSTREAM.replace(
                "allow_download: true,",
                "allow_download: bool::from(true),",
                1,
            )
            + _INSTALL_DEFAULT_COMMENT_DECOY,
            "InstallOptions allow_download default",
        ),
        (
            "allow_default_manifest_url: bool::from(true),".join(
                _MESH_UPSTREAM.rsplit("allow_default_manifest_url: true,", 1)
            )
            + _INSTALL_MANIFEST_COMMENT_DECOY,
            "install_native_runtime allow_default_manifest_url hardcode",
        ),
    ],
    ids=["manifest-default", "install-default", "install-manifest"],
)
def test_desktop_vendor_patch_rejects_policy_matches_hidden_in_comments(
    tmp_path: Path,
    source: str,
    context: str,
) -> None:
    """Commented decoys cannot make an active network policy look patched."""
    mesh_path = _write_mesh_vendor(tmp_path, source)
    sherpa_path = _write_sherpa_vendor(tmp_path)

    with pytest.raises(_patcher().RuntimePolicyPatchError, match=context):
        _patcher().patch_desktop_cargo_deps(tmp_path)

    assert mesh_path.read_text(encoding="utf-8") == source
    assert sherpa_path.read_text(encoding="utf-8") == _SHERPA_BUILD_UPSTREAM


@pytest.mark.parametrize(
    ("header", "context"),
    [
        (
            "impl Default for NativeRuntimeManifestOptions {",
            "ManifestOptions allow_default_manifest_url default",
        ),
        (
            "impl Default for NativeRuntimeInstallOptions {",
            "InstallOptions allow_download default",
        ),
        (
            "pub async fn install_native_runtime(",
            "install_native_runtime allow_default_manifest_url hardcode",
        ),
    ],
    ids=["manifest-default", "install-default", "install-entrypoint"],
)
def test_desktop_vendor_patch_rejects_disabled_reviewed_policy_items(
    tmp_path: Path,
    header: str,
    context: str,
) -> None:
    """A cfg-disabled policy item cannot attest the compiled vendor behavior."""
    source = _MESH_UPSTREAM.replace(header, f"#[cfg(any())]\n{header}", 1)
    mesh_path = _write_mesh_vendor(tmp_path, source)
    sherpa_path = _write_sherpa_vendor(tmp_path)

    with pytest.raises(_patcher().RuntimePolicyPatchError, match=context):
        _patcher().patch_desktop_cargo_deps(tmp_path)

    assert mesh_path.read_text(encoding="utf-8") == source
    assert sherpa_path.read_text(encoding="utf-8") == _SHERPA_BUILD_UPSTREAM


def test_desktop_vendor_patch_rejects_policy_items_in_disabled_module(
    tmp_path: Path,
) -> None:
    """Exact policy items nested in disabled code cannot attest active defaults."""
    patcher = _patcher()
    source = (
        _MESH_UPSTREAM
        .replace(
            "impl Default for NativeRuntimeManifestOptions {",
            "impl  Default for NativeRuntimeManifestOptions {",
            1,
        )
        .replace(
            "impl Default for NativeRuntimeInstallOptions {",
            "impl  Default for NativeRuntimeInstallOptions {",
            1,
        )
        .replace(
            "pub async fn install_native_runtime(",
            "pub async  fn install_native_runtime(",
            1,
        )
        + "\n#[cfg(any())]\nmod decoy {\n"
        + patcher._MANIFEST_DEFAULT_UPSTREAM
        + "\n"
        + patcher._INSTALL_DEFAULT_UPSTREAM
        + "\n"
        + patcher._INSTALL_NATIVE_RUNTIME_UPSTREAM
        + "\n}\n"
    )
    mesh_path = _write_mesh_vendor(tmp_path, source)
    sherpa_path = _write_sherpa_vendor(tmp_path)

    with pytest.raises(
        patcher.RuntimePolicyPatchError,
        match="ManifestOptions allow_default_manifest_url default",
    ):
        patcher.patch_desktop_cargo_deps(tmp_path)

    assert mesh_path.read_text(encoding="utf-8") == source
    assert sherpa_path.read_text(encoding="utf-8") == _SHERPA_BUILD_UPSTREAM


@pytest.mark.parametrize(
    "source_prefix",
    ["#![cfg(any())]\n", "\ufeff#![cfg(any())]\n"],
    ids=["plain", "utf8-bom"],
)
def test_desktop_vendor_patch_rejects_file_level_disable_attribute(
    tmp_path: Path,
    source_prefix: str,
) -> None:
    """A file-level cfg cannot make reviewed policy items non-executable."""
    source = source_prefix + _MESH_UPSTREAM
    mesh_path = _write_mesh_vendor(tmp_path, source)
    sherpa_path = _write_sherpa_vendor(tmp_path)

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="ManifestOptions allow_default_manifest_url default",
    ):
        _patcher().patch_desktop_cargo_deps(tmp_path)

    assert mesh_path.read_text(encoding="utf-8") == source
    assert sherpa_path.read_text(encoding="utf-8") == _SHERPA_BUILD_UPSTREAM


def test_desktop_vendor_patch_rejects_later_policy_reenable(
    tmp_path: Path,
) -> None:
    """Changing an initializer is insufficient when later code restores true."""
    reviewed_return = """        Self {
            mesh_version: CURRENT_MESH_VERSION.to_string(),
            manifest_path: None,
            manifest_url: None,
            bundle_dirs: Vec::new(),
            allow_default_manifest_url: true,
        }
"""
    drifted_return = """        let mut options = Self {
            mesh_version: CURRENT_MESH_VERSION.to_string(),
            manifest_path: None,
            manifest_url: None,
            bundle_dirs: Vec::new(),
            allow_default_manifest_url: true,
        };
        options.allow_default_manifest_url = true;
        options
"""
    source = _MESH_UPSTREAM.replace(reviewed_return, drifted_return, 1)
    mesh_path = _write_mesh_vendor(tmp_path, source)
    sherpa_path = _write_sherpa_vendor(tmp_path)

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="ManifestOptions allow_default_manifest_url default",
    ):
        _patcher().patch_desktop_cargo_deps(tmp_path)

    assert mesh_path.read_text(encoding="utf-8") == source
    assert sherpa_path.read_text(encoding="utf-8") == _SHERPA_BUILD_UPSTREAM


@pytest.mark.parametrize(
    "source",
    [
        _SHERPA_BUILD_UPSTREAM.replace('    "piper_phonemize",\n', "", 1),
        _SHERPA_BUILD_UPSTREAM + _SHERPA_STATIC_LIBS_UPSTREAM,
    ],
    ids=["drifted", "duplicate"],
)
def test_desktop_vendor_patch_rejects_sherpa_static_link_list_drift_atomically(
    tmp_path: Path,
    source: str,
) -> None:
    """Sherpa list drift must leave both copied vendor crates byte-identical."""
    mesh_path = _write_mesh_vendor(tmp_path)
    sherpa_path = _write_sherpa_vendor(tmp_path, source)

    with pytest.raises(
        _patcher().RuntimePolicyPatchError,
        match="sherpa-onnx-sys static link list",
    ):
        _patcher().patch_desktop_cargo_deps(tmp_path)

    assert mesh_path.read_text(encoding="utf-8") == _MESH_UPSTREAM
    assert sherpa_path.read_text(encoding="utf-8") == source


def test_runtime_policy_contract_distinguishes_runtime_and_updater_pairs() -> None:
    """Runtime path requirements must not blur the updater's two-value gate."""
    contracts = expect_instance(
        expect_binding(_package_scope(), "expectedNativeContracts").value,
        AttributeSet,
    )
    runtime_policy = expect_binding(contracts.values, "patchedBuzzSource").value

    assert_nix_ast_equal(
        runtime_policy,
        """{
          kind = "buzz-runtime-policy-source";
          commit = expectedCommit;
          meshFeature = "dynamic-native-runtime";
          runtimeBundleEnvironment = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
          runtimeCacheEnvironment = "MESH_LLM_NATIVE_RUNTIME_CACHE_DIR";
          manifestUrlEnvironment = "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL";
          requiresBothRuntimeEnvironmentValues = true;
          manifestUrlEnvironmentAllowed = false;
          allowDefaultManifestUrl = false;
          allowDownload = false;
          keyringProbePolicy = {
            preUiInteractionAllowed = false;
            interactionGuard = "security-framework-raii";
            interactionGuardScope = "tauri-setup";
            guardFailure = "keyring-locked";
            unexpectedReadFailure = "unreachable";
            managedAgentSecretLoadsAllowedInRecovery = false;
            identityResolutionUsesReadonlyLoad = true;
            identityResolutionLegacyMigrationAllowed = false;
            postUiInteractionAllowed = true;
            postUiRetryCommand = "retry_keyring_identity";
            postUiRetryUsesExistingIdentity = true;
            postUiRetryMutationAllowed = false;
            postUiRetrySerializedBy = "identity_mutation";
            postUiRetryRequiresRelaunch = true;
          };
          sherpaOnnxTtsEnabled = false;
          sherpaOnnxStaticLinkLibraries = [
            "sherpa-onnx-c-api"
            "sherpa-onnx-core"
            "kaldi-decoder-core"
            "sherpa-onnx-kaldifst-core"
            "sherpa-onnx-fstfar"
            "sherpa-onnx-fst"
            "kaldi-native-fbank-core"
            "kissfft-float"
            "onnxruntime"
            "ssentencepiece_core"
          ];
          updaterRequiresBothEnvironmentValues = true;
        }""",
    )


def test_package_wires_the_runtime_policy_source_and_patched_vendor() -> None:
    """The non-null slot and package passthru must select only patched copies."""
    scope = _package_scope()
    assert_nix_ast_equal(
        expect_binding(scope, "buzzRuntimePolicySource").value,
        """import ./native/buzz-runtime-policy.nix {
          inherit desktopCargoDeps nativeLock python3 src stdenvNoCC version;
          expectedContract = expectedNativeContracts.patchedBuzzSource;
        }""",
    )
    slots = expect_instance(
        expect_binding(scope, "nativeFoundationSlots").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(slots.values, "patchedBuzzSource").value,
        "buzzRuntimePolicySource",
    )
    passthru = expect_instance(
        expect_binding(scope, "commonPassthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "desktopCargoDeps").value,
        "buzzRuntimePolicySource.passthru.desktopCargoDeps",
    )


def test_runtime_policy_derivations_copy_then_patch_normal_outputs() -> None:
    """Nix must patch normal copied source and vendor outputs without new hashes."""
    policy_path = _PACKAGE_DIR / "native/buzz-runtime-policy.nix"

    assert_nix_ast_equal(
        policy_path.read_text(encoding="utf-8"),
        """
        {
          desktopCargoDeps,
          expectedContract,
          nativeLock ? builtins.fromJSON (builtins.readFile ../native-lock.json),
          python3,
          src,
          stdenvNoCC,
          version,
        }:
        let
          patcher = ./patch_runtime_policy.py;
          buzzCommit = nativeLock.buzz.commit or null;
          implementedContract = {
            kind = "buzz-runtime-policy-source";
            commit = buzzCommit;
            meshFeature = "dynamic-native-runtime";
            runtimeBundleEnvironment = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
            runtimeCacheEnvironment = "MESH_LLM_NATIVE_RUNTIME_CACHE_DIR";
            manifestUrlEnvironment = "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL";
            requiresBothRuntimeEnvironmentValues = true;
            manifestUrlEnvironmentAllowed = false;
            allowDefaultManifestUrl = false;
            allowDownload = false;
            keyringProbePolicy = {
              preUiInteractionAllowed = false;
              interactionGuard = "security-framework-raii";
              interactionGuardScope = "tauri-setup";
              guardFailure = "keyring-locked";
              unexpectedReadFailure = "unreachable";
              managedAgentSecretLoadsAllowedInRecovery = false;
              identityResolutionUsesReadonlyLoad = true;
              identityResolutionLegacyMigrationAllowed = false;
              postUiInteractionAllowed = true;
              postUiRetryCommand = "retry_keyring_identity";
              postUiRetryUsesExistingIdentity = true;
              postUiRetryMutationAllowed = false;
              postUiRetrySerializedBy = "identity_mutation";
              postUiRetryRequiresRelaunch = true;
            };
            sherpaOnnxTtsEnabled = false;
            sherpaOnnxStaticLinkLibraries = [
              "sherpa-onnx-c-api"
              "sherpa-onnx-core"
              "kaldi-decoder-core"
              "sherpa-onnx-kaldifst-core"
              "sherpa-onnx-fstfar"
              "sherpa-onnx-fst"
              "kaldi-native-fbank-core"
              "kissfft-float"
              "onnxruntime"
              "ssentencepiece_core"
            ];
            updaterRequiresBothEnvironmentValues = true;
          };
          patchedDesktopCargoDeps = stdenvNoCC.mkDerivation {
            pname = "buzz-desktop-cargo-deps-runtime-policy";
            inherit version;
            dontUnpack = true;
            dontFixup = true;
            installPhase = ''
              runHook preInstall
              mkdir -p "$out"
              cp -R ${desktopCargoDeps}/. "$out/"
              chmod -R u+w "$out"
              ${python3}/bin/python3 ${patcher} desktop-cargo-deps "$out"
              runHook postInstall
            '';
          };
        in
        assert builtins.isString buzzCommit && builtins.match "[0-9a-f]{40}" buzzCommit != null;
        assert expectedContract == implementedContract;
        stdenvNoCC.mkDerivation {
          pname = "buzz-runtime-policy-source";
          inherit src version;
          dontConfigure = true;
          dontBuild = true;
          dontFixup = true;
          installPhase = ''
            runHook preInstall
            cp -R . "$out"
            chmod -R u+w "$out"
            ${python3}/bin/python3 ${patcher} buzz-source "$out"
            runHook postInstall
          '';
          passthru = {
            buzzNativeContract = implementedContract;
            desktopCargoDeps = patchedDesktopCargoDeps;
            requiredLaunchEnvironment = {
              requiredAbsolutePathVariables = [
                implementedContract.runtimeBundleEnvironment
                implementedContract.runtimeCacheEnvironment
              ];
              forbiddenNonblankVariables = [ implementedContract.manifestUrlEnvironment ];
            };
          };
        }
        """,
    )


def test_build_plan_exposes_launch_policy_without_claiming_a_wrapper() -> None:
    """The eventual app gets launch requirements, not an unimplemented wrapper."""
    passthru = expect_instance(
        expect_binding(_package_scope(), "commonPassthru").value,
        AttributeSet,
    )
    plan = expect_instance(
        expect_binding(passthru.values, "buzzNativeBuildPlan").value,
        AttributeSet,
    )
    native_runtime = expect_instance(
        expect_binding(plan.values, "nativeRuntime").value,
        AttributeSet,
    )
    replacement = expect_instance(
        expect_binding(native_runtime.values, "requiredReplacement").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(replacement.values, "launchEnvironment").value,
        "buzzRuntimePolicySource.passthru.requiredLaunchEnvironment",
    )
