"""Apply Buzz and Mesh native-runtime policy patches to copied source trees."""

import argparse
import re
from pathlib import Path

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
_MESH_RUNTIME_INSTALL_GLOB = "**/mesh-llm-runtime-install-0.75.1/src/lib.rs"
_SHERPA_ONNX_SYS_BUILD_GLOB = "**/sherpa-onnx-sys-1.13.4/build.rs"
_RUST_INNER_ATTRIBUTE = re.compile(r"(?m)^(?:\ufeff)?[ \t]*#\s*!\s*\[")
_BUZZ_APP_INNER_ATTRIBUTE = (
    '#![recursion_limit = "256"] '
    "// Deep Tauri command futures exceed the default layout query depth.\n"
)

_BUZZ_STARTUP_ANCHOR = """async fn initialize_mesh_native_runtime() -> anyhow::Result<()> {
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
_BUZZ_GUARDED_STARTUP = """fn require_absolute_runtime_environment(
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

_BUZZ_APP_IMPORTS_UPSTREAM = """use std::sync::{atomic::AtomicBool, atomic::Ordering, Arc};
#[cfg(target_os = "macos")]
use tauri::Listener;
"""
_BUZZ_APP_IMPORTS_KEYCHAIN = """#[cfg(all(feature = "system-keyring", target_os = "macos"))]
use security_framework::os::macos::keychain::SecKeychain;
use std::sync::{atomic::AtomicBool, atomic::Ordering, Arc};
#[cfg(target_os = "macos")]
use tauri::Listener;
"""

_BUZZ_SETUP_UPSTREAM = """        .setup(move |app| {
            let app_handle = app.handle().clone();
            #[cfg(target_os = "macos")]
"""
_BUZZ_SETUP_KEYCHAIN_GUARDED = """        .setup(move |app| {
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
"""

_BUZZ_BACKFILL_UPSTREAM = """            if let Err(e) = backfill_persona_snapshots(&app_handle) {
                eprintln!("buzz-desktop: persona-snapshot backfill failed: {e}");
            }
"""
_BUZZ_BACKFILL_RECOVERY_GUARDED = """            if !recovery_mode {
                if let Err(e) = backfill_persona_snapshots(&app_handle) {
                    eprintln!("buzz-desktop: persona-snapshot backfill failed: {e}");
                }
            }
"""
_BUZZ_NEST_REGENERATION_UPSTREAM = """            try_regenerate_nest(&app_handle);
"""
_BUZZ_NEST_REGENERATION_RECOVERY_GUARDED = """            if !recovery_mode {
                try_regenerate_nest(&app_handle);
            }
"""
_BUZZ_IDENTITY_HANDLER_UPSTREAM = """            get_identity,
            get_nsec,
"""
_BUZZ_IDENTITY_HANDLER_WITH_RETRY = """            get_identity,
            retry_keyring_identity,
            get_nsec,
"""

_BUZZ_IDENTITY_STORE_IMPL_UPSTREAM = """impl IdentityKeyStore for crate::secret_store::SecretStore {
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
_BUZZ_IDENTITY_STORE_IMPL_READONLY = _BUZZ_IDENTITY_STORE_IMPL_UPSTREAM.replace(
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
_BUZZ_IDENTITY_STORE_IMPL_WITH_RETRY = (
    _BUZZ_IDENTITY_STORE_IMPL_READONLY
    + """

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
)

_BUZZ_DEFAULT_RELAY_COMMAND_UPSTREAM = """#[tauri::command]
pub fn get_default_relay_url() -> String {
    relay::relay_ws_url()
}
"""
_BUZZ_DEFAULT_RELAY_COMMAND_WITH_RETRY = """#[tauri::command]
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

_BUZZ_TAURI_GET_NSEC_UPSTREAM = """export async function getNsec(): Promise<string> {
  return invokeTauri<string>("get_nsec");
}
"""
_BUZZ_TAURI_GET_NSEC_WITH_RETRY = (
    _BUZZ_TAURI_GET_NSEC_UPSTREAM
    + """
/** Retry access to the existing system-keyring identity after the UI is visible. */
export async function retryKeyringIdentity(): Promise<void> {
  await invokeTauri("retry_keyring_identity");
}
"""
)

_BUZZ_KEYRING_IMPORT_UPSTREAM = (
    'import { importIdentity } from "@/shared/api/tauriIdentity";\n'
)
_BUZZ_KEYRING_IMPORT_WITH_RETRY = """import {
  importIdentity,
  retryKeyringIdentity,
} from "@/shared/api/tauriIdentity";
"""
_BUZZ_KEYRING_STATE_UPSTREAM = """  const [showImport, setShowImport] = React.useState(false);

  const handleReimportClick = React.useCallback(() => {
"""
_BUZZ_KEYRING_STATE_WITH_RETRY = """  const [showImport, setShowImport] = React.useState(false);
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
"""
_BUZZ_KEYRING_DESCRIPTION_UPSTREAM = (
    '        <p className="mt-3 text-sm leading-6 text-muted-foreground">\n'
    """          Your identity is safe in the OS keyring, but it's unreachable this
          session. Unlock your keyring or sign into your desktop session, then
          relaunch Buzz.
        </p>
"""
)
_BUZZ_KEYRING_DESCRIPTION_WITH_RETRY = (
    '        <p className="mt-3 text-sm leading-6 text-muted-foreground">\n'
    """          Your identity is safe in the OS keyring, but Buzz could not access it
          before this window opened. Retry now so macOS can ask you to authorize
          this copy of Buzz, then Buzz will relaunch automatically.
        </p>
"""
)
_BUZZ_KEYRING_RELAUNCH_UPSTREAM = """            <Button
              className="h-10 w-full"
              data-testid="relaunch-app"
              onClick={() => {
                void relaunch();
              }}
              type="button"
            >
              Relaunch Buzz
            </Button>
"""
_BUZZ_KEYRING_RELAUNCH_WITH_RETRY = """            <Button
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
"""

_BUZZ_E2E_GET_IDENTITY_CASE_UPSTREAM = '      case "get_identity": {\n'
_BUZZ_E2E_GET_IDENTITY_CASE_WITH_RETRY = """      case "retry_keyring_identity":
        return;
      case "get_identity": {
"""

_BUZZ_E2E_RELAUNCH_TEST_UPSTREAM = (
    'test("locked screen relaunch button records the process-restart invoke", '
    """async ({
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
)
_BUZZ_E2E_RELAUNCH_TEST_WITH_RETRY = (
    'test("locked screen retries the existing key and relaunches after success", '
    """async ({
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
)

_BUZZ_APP_STATE_TEST_ANCHOR = """#[test]
fn corrupt_keyring_recovers_valid_file_without_rotating() {
"""
_BUZZ_APP_STATE_RETRY_TESTS = (
    """struct RetryUnavailableIdentityStore;

impl IdentityKeyStore for RetryUnavailableIdentityStore {
    fn probe(&self, _name: &str) -> KeyringProbe {
        KeyringProbe::Unreachable
    }
    fn load(&self, _name: &str) -> Result<Option<String>, String> {
        Err("simulated keyring read failure".to_string())
    }
    fn store(&self, _name: &str, _value: &str) -> Result<(), String> {
        panic!("retry must not store")
    }
    fn delete(&self, _name: &str) -> Result<(), String> {
        panic!("retry must not delete")
    }
    fn verify_stored(&self, _key: &str, _expected: &str) -> Result<bool, String> {
        panic!("retry must not verify a write")
    }
}

#[test]
fn retry_locked_identity_authorizes_without_mutating_recovery_state() {
    let expected = Keys::generate();
    let nsec = expected.secret_key().to_bech32().unwrap();
    // Any store attempt fails, while sentinel AppState values prove success
    // only authorizes/parses the existing key for a required fresh launch.
    let store = FakeIdentityStore::present_with_store_failing(&nsec);
    let state = build_app_state();
    let ephemeral = Keys::generate();
    let ephemeral_pubkey = ephemeral.public_key();
    *state.keys.lock().unwrap() = ephemeral;
    state.set_identity_storage(IdentityStorage::Ephemeral);
    state
        .identity_lost
        .store(true, std::sync::atomic::Ordering::Release);
    state
        .keyring_locked
        .store(true, std::sync::atomic::Ordering::Release);

    retry_locked_identity_with_store(&state, &store).unwrap();

    assert_eq!(state.keys.lock().unwrap().public_key(), ephemeral_pubkey);
    assert!(state
        .keyring_locked
        .load(std::sync::atomic::Ordering::Acquire));
    assert!(state
        .identity_lost
        .load(std::sync::atomic::Ordering::Acquire));
    assert_eq!(state.identity_storage(), IdentityStorage::Ephemeral);
    assert!(store.deleted.borrow().is_empty());
    assert_eq!(store.slot.borrow().get(IDENTITY_KEY_NAME), Some(&nsec));
}

#[test]
fn retry_locked_identity_preserves_recovery_state_while_keyring_is_unreachable() {
    let store = RetryUnavailableIdentityStore;
    let state = build_app_state();
    let ephemeral = Keys::generate();
    let ephemeral_pubkey = ephemeral.public_key();
    *state.keys.lock().unwrap() = ephemeral;
    state.set_identity_storage(IdentityStorage::Ephemeral);
    state
        .keyring_locked
        .store(true, std::sync::atomic::Ordering::Release);

    let error = retry_locked_identity_with_store(&state, &store).unwrap_err();

    assert!(error.contains("still unavailable"));
    assert_eq!(state.keys.lock().unwrap().public_key(), ephemeral_pubkey);
    assert!(state
        .keyring_locked
        .load(std::sync::atomic::Ordering::Acquire));
    assert_eq!(state.identity_storage(), IdentityStorage::Ephemeral);
}

#[test]
fn dpk_blob_only_identity_resolves_as_present_instead_of_lost() {
    let dir = tempfile::tempdir().unwrap();
    let legacy_path = dir.path().join("identity.key");
    std::fs::write(migration_marker_path(dir.path()), b"1").unwrap();
    let expected = Keys::generate();
    let nsec = expected.secret_key().to_bech32().unwrap();
    // The production DPK-blob probe maps this legacy shape to Present; this
    // resolver assertion proves that outcome never enters Lost recovery.
    let store = FakeIdentityStore::present_with(&nsec);

    let resolved = resolve_identity_with_store(&store, &legacy_path, dir.path()).unwrap();

    assert_key_eq(&expected, &resolved.keys);
    assert_ne!(resolved.recovery, RecoveryState::Lost);
    assert_eq!(resolved.recovery, RecoveryState::None);
    assert_eq!(resolved.storage, IdentityStorage::SystemKeyring);
}

#[test]
fn post_retry_relaunch_reads_existing_identity_without_keyring_writes() {
    let dir = tempfile::tempdir().unwrap();
    let legacy_path = dir.path().join("identity.key");
    std::fs::write(migration_marker_path(dir.path()), b"1").unwrap();
    let expected = Keys::generate();
    let nsec = expected.secret_key().to_bech32().unwrap();
    // A store attempt fails, so a successful normal resolver proves the
    // production identity seam is read-only on the relaunch after Retry.
    let store = FakeIdentityStore::present_with_store_failing(&nsec);

    let resolved = resolve_identity_with_store(&store, &legacy_path, dir.path()).unwrap();

    assert_key_eq(&expected, &resolved.keys);
    assert_eq!(resolved.recovery, RecoveryState::None);
    assert_eq!(resolved.storage, IdentityStorage::SystemKeyring);
    assert!(store.deleted.borrow().is_empty());
    assert_eq!(store.slot.borrow().get(IDENTITY_KEY_NAME), Some(&nsec));
}

"""
    + _BUZZ_APP_STATE_TEST_ANCHOR
)

_DPK_PROBE_UPSTREAM = """\
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
"""
_DPK_PROBE_FAIL_CLOSED = """\
    /// Check every old DPK/keyring shape for `key` without migration.
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

_LEGACY_KEYRING_PROBE_UPSTREAM = """    #[cfg(feature = "system-keyring")]
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
"""
_LEGACY_KEYRING_PROBE_FAIL_CLOSED = """    #[cfg(feature = "system-keyring")]
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
"""

_KEYCHAIN_LOAD_ALL_READONLY_UPSTREAM = (
    "    /// Read the secret for `key` without any legacy-migration side effects.\n"
    """    ///
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
"""
)
_KEYCHAIN_LOAD_ALL_READONLY_WITH_KEY = (
    "    /// Read one secret without legacy-migration writes or deletes.\n"
    """    ///
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
"""
)

_KEYRING_STORE_TEST_ANCHOR = """    #[test]
    fn probe_returns_present_when_key_in_cache() {
"""
_KEYRING_STORE_READONLY_TESTS = (
    """    fn identity_map(value: &str) -> HashMap<String, String> {
        HashMap::from([("identity".to_string(), value.to_string())])
    }

    fn fake_readonly_lookup(
        main_blob: Option<HashMap<String, String>>,
        dpk_blob: Option<HashMap<String, String>>,
        dpk_key: Option<String>,
        legacy_key: Option<String>,
    ) -> (Option<String>, Vec<&'static str>) {
        let (result, calls) = fake_readonly_lookup_with_dpk_results(
            main_blob,
            Ok(dpk_blob),
            Ok(dpk_key),
            legacy_key,
        );
        (result.unwrap(), calls)
    }

    fn fake_readonly_lookup_with_dpk_results(
        main_blob: Option<HashMap<String, String>>,
        dpk_blob: Result<Option<HashMap<String, String>>, String>,
        dpk_key: Result<Option<String>, String>,
        legacy_key: Option<String>,
    ) -> (Result<Option<String>, String>, Vec<&'static str>) {
        let calls = std::cell::RefCell::new(Vec::new());
        let result = SecretStore::resolve_readonly_lookup(
            "identity",
            || {
                calls.borrow_mut().push("main-blob");
                Ok(main_blob)
            },
            || {
                calls.borrow_mut().push("dpk-blob");
                dpk_blob
            },
            || {
                calls.borrow_mut().push("dpk-key");
                dpk_key
            },
            || {
                calls.borrow_mut().push("legacy-keyring");
                Ok(legacy_key)
            },
        );
        (result, calls.into_inner())
    }

    #[test]
    fn dpk_blob_probe_routes_identity_to_present_without_fallback() {
        let fallback_called = std::cell::Cell::new(false);
        let probe = SecretStore::classify_legacy_dpk_blob_probe(
            "identity",
            Ok(Some(identity_map("dpk-blob"))),
            || {
                fallback_called.set(true);
                KeyringProbe::ReachableButEmpty
            },
        );

        assert_eq!(probe, KeyringProbe::Present);
        assert!(!fallback_called.get());
    }

    #[test]
    fn dpk_probe_missing_entitlement_preserves_only_a_present_fallback() {
        assert_eq!(
            SecretStore::classify_legacy_dpk_blob_probe(
                "identity",
                Err("missing entitlement".to_string()),
                || KeyringProbe::Present,
            ),
            KeyringProbe::Present,
        );
        assert_eq!(
            SecretStore::classify_legacy_dpk_blob_probe(
                "identity",
                Err("missing entitlement".to_string()),
                || KeyringProbe::ReachableButEmpty,
            ),
            KeyringProbe::Unreachable,
        );
    }

    #[test]
    fn unknown_dpk_key_error_preserves_only_a_present_legacy_fallback() {
        assert_eq!(
            SecretStore::classify_inaccessible_dpk_fallback(KeyringProbe::Present),
            KeyringProbe::Present,
        );
        assert_eq!(
            SecretStore::classify_inaccessible_dpk_fallback(
                KeyringProbe::ReachableButEmpty,
            ),
            KeyringProbe::Unreachable,
        );
    }

    #[test]
    fn readonly_lookup_checks_every_existing_key_shape_in_order() {
        assert_eq!(
            fake_readonly_lookup(Some(identity_map("main")), None, None, None),
            (Some("main".to_string()), vec!["main-blob"]),
        );
        assert_eq!(
            fake_readonly_lookup(None, Some(identity_map("dpk-blob")), None, None),
            (
                Some("dpk-blob".to_string()),
                vec!["main-blob", "dpk-blob"],
            ),
        );
        assert_eq!(
            fake_readonly_lookup(None, None, Some("dpk-key".to_string()), None),
            (
                Some("dpk-key".to_string()),
                vec!["main-blob", "dpk-blob", "dpk-key"],
            ),
        );
        assert_eq!(
            fake_readonly_lookup(None, None, None, Some("legacy".to_string())),
            (
                Some("legacy".to_string()),
                vec!["main-blob", "dpk-blob", "dpk-key", "legacy-keyring"],
            ),
        );
    }

    #[test]
    fn readonly_lookup_defers_dpk_unavailable_only_for_a_present_fallback() {
        let (fallback, fallback_calls) = fake_readonly_lookup_with_dpk_results(
            None,
            Err("missing entitlement".to_string()),
            Err("missing entitlement".to_string()),
            Some("legacy".to_string()),
        );
        assert_eq!(fallback, Ok(Some("legacy".to_string())));
        assert_eq!(
            fallback_calls,
            vec!["main-blob", "dpk-blob", "dpk-key", "legacy-keyring"],
        );

        let (unavailable, unavailable_calls) = fake_readonly_lookup_with_dpk_results(
            None,
            Err("missing entitlement".to_string()),
            Err("missing entitlement".to_string()),
            None,
        );
        assert_eq!(unavailable.unwrap_err(), "missing entitlement");
        assert_eq!(
            unavailable_calls,
            vec!["main-blob", "dpk-blob", "dpk-key", "legacy-keyring"],
        );
    }

    #[test]
    fn readonly_lookup_uses_dpk_key_after_dpk_blob_error() {
        let (result, calls) = fake_readonly_lookup_with_dpk_results(
            None,
            Err("dpk blob unavailable".to_string()),
            Ok(Some("dpk-key".to_string())),
            None,
        );
        assert_eq!(result, Ok(Some("dpk-key".to_string())));
        assert_eq!(
            calls,
            vec!["main-blob", "dpk-blob", "dpk-key"],
        );
    }

"""
    + _KEYRING_STORE_TEST_ANCHOR
)

_MANIFEST_DEFAULT_UPSTREAM = """impl Default for NativeRuntimeManifestOptions {
    fn default() -> Self {
        Self {
            mesh_version: CURRENT_MESH_VERSION.to_string(),
            manifest_path: None,
            manifest_url: None,
            bundle_dirs: Vec::new(),
            allow_default_manifest_url: true,
        }
    }
}"""
_MANIFEST_DEFAULT_PATCHED = _MANIFEST_DEFAULT_UPSTREAM.replace(
    "allow_default_manifest_url: true,",
    "allow_default_manifest_url: false,",
)

_INSTALL_DEFAULT_UPSTREAM = """impl Default for NativeRuntimeInstallOptions {
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
}"""
_INSTALL_DEFAULT_PATCHED = _INSTALL_DEFAULT_UPSTREAM.replace(
    "allow_download: true,",
    "allow_download: false,",
)

_INSTALL_NATIVE_RUNTIME_UPSTREAM = """pub async fn install_native_runtime(
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
}"""
_INSTALL_NATIVE_RUNTIME_PATCHED = _INSTALL_NATIVE_RUNTIME_UPSTREAM.replace(
    "allow_default_manifest_url: true,",
    "allow_default_manifest_url: false,",
)

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


class RuntimePolicyPatchError(RuntimeError):
    """Report source drift that makes a reviewed policy patch unsafe."""


def _mask_span(masked: list[str], source: str, start: int, end: int) -> None:
    """Blank one non-code span without changing offsets or line boundaries."""
    for index in range(start, end):
        if source[index] not in "\r\n":
            masked[index] = " "


def _rust_char_literal_end(source: str, start: int) -> int | None:
    """Return the exclusive end of one Rust character literal, not a lifetime."""
    cursor = start + 1
    if cursor >= len(source) or source[cursor] in "'\r\n":
        return None
    if source[cursor] == "\\":
        cursor += 1
        if cursor >= len(source):
            return None
        if source[cursor] == "x":
            cursor += 3
        elif source.startswith("u{", cursor):
            closing_brace = source.find("}", cursor + 2)
            if closing_brace < 0:
                return None
            cursor = closing_brace + 1
        else:
            cursor += 1
    else:
        cursor += 1
    if cursor < len(source) and source[cursor] == "'":
        return cursor + 1
    return None


def _rust_block_comment_end(source: str, start: int) -> int:
    """Return the end of one possibly nested Rust block comment."""
    cursor = start + 2
    depth = 1
    while cursor < len(source) and depth:
        if source.startswith("/*", cursor):
            depth += 1
            cursor += 2
        elif source.startswith("*/", cursor):
            depth -= 1
            cursor += 2
        else:
            cursor += 1
    if depth:
        message = "Rust source contains an unterminated block comment"
        raise RuntimePolicyPatchError(message)
    return cursor


def _rust_raw_string_span(source: str, start: int) -> tuple[int, int, int] | None:
    """Return content start, content end, and literal end for a raw string."""
    raw_marker = start + int(source.startswith(("br", "cr"), start))
    if source[raw_marker] != "r":
        return None
    opening_quote = raw_marker + 1
    while opening_quote < len(source) and source[opening_quote] == "#":
        opening_quote += 1
    if opening_quote >= len(source) or source[opening_quote] != '"':
        return None
    hashes = source[raw_marker + 1 : opening_quote]
    closing_marker = '"' + hashes
    closing_quote = source.find(closing_marker, opening_quote + 1)
    if closing_quote < 0:
        message = "Rust source contains an unterminated raw string literal"
        raise RuntimePolicyPatchError(message)
    return opening_quote + 1, closing_quote, closing_quote + len(closing_marker)


def _rust_string_end(source: str, start: int) -> int:
    """Return the exclusive end of one escaped Rust string literal."""
    cursor = start + 1
    while cursor < len(source):
        if source[cursor] == "\\":
            cursor += 2
        elif source[cursor] == '"':
            return cursor + 1
        else:
            cursor += 1
    message = "Rust source contains an unterminated string literal"
    raise RuntimePolicyPatchError(message)


def mask_rust_non_code(source: str) -> str:
    """Mask Rust comments and literal contents while preserving every offset."""
    masked = list(source)
    cursor = 0
    while cursor < len(source):
        if source.startswith("//", cursor):
            end = source.find("\n", cursor + 2)
            if end < 0:
                end = len(source)
            _mask_span(masked, source, cursor, end)
            cursor = end
            continue

        if source.startswith("/*", cursor):
            end = _rust_block_comment_end(source, cursor)
            _mask_span(masked, source, cursor, end)
            cursor = end
            continue

        raw_span = _rust_raw_string_span(source, cursor)
        if raw_span is not None:
            content_start, content_end, cursor = raw_span
            _mask_span(masked, source, content_start, content_end)
            continue

        if source[cursor] == '"':
            string_end = _rust_string_end(source, cursor)
            _mask_span(masked, source, cursor + 1, string_end - 1)
            cursor = string_end
            continue

        if source[cursor] == "'":
            char_end = _rust_char_literal_end(source, cursor)
            if char_end is not None:
                _mask_span(masked, source, cursor + 1, char_end - 1)
                cursor = char_end
                continue

        cursor += 1
    return "".join(masked)


def rust_item_has_outer_attribute(source: str, start: int) -> bool:
    """Return whether an attribute is directly attached to a Rust item."""
    cursor = start - 1
    while cursor >= 0 and source[cursor].isspace():
        cursor -= 1
    if cursor < 0 or source[cursor] != "]":
        return False
    depth = 1
    cursor -= 1
    while cursor >= 0 and depth:
        if source[cursor] == "]":
            depth += 1
        elif source[cursor] == "[":
            depth -= 1
        cursor -= 1
    if depth:
        return True
    while cursor >= 0 and source[cursor].isspace():
        cursor -= 1
    return cursor >= 0 and source[cursor] == "#"


def rust_delimiter_stack(source: str, end: int) -> tuple[str, ...] | None:
    """Return unmatched lexical delimiters before one masked-source offset."""
    stack: list[str] = []
    opening = "({["
    closing = {")": "(", "}": "{", "]": "["}
    for character in source[:end]:
        if character in opening:
            stack.append(character)
        elif character in closing:
            if not stack or stack[-1] != closing[character]:
                return None
            stack.pop()
    return tuple(stack)


def rust_file_has_inner_attribute(source: str, end: int) -> bool:
    """Return whether an active root inner attribute precedes one item."""
    return any(
        rust_delimiter_stack(source, match.start()) == ()
        for match in _RUST_INNER_ATTRIBUTE.finditer(source, 0, end)
    )


def _replace_reviewed_rust_item(
    text: str,
    before: str,
    after: str,
    *,
    context: str,
    allow_file_inner_attributes: bool = False,
    expected_delimiters: tuple[str, ...] = (),
) -> str:
    """Replace one active, attribute-free, byte-reviewed Rust item."""
    masked_text = mask_rust_non_code(text)
    masked_before = mask_rust_non_code(before)
    count = masked_text.count(masked_before)
    if count != 1:
        message = f"{context}: expected exactly 1 reviewed item, found {count}"
        raise RuntimePolicyPatchError(message)
    start = masked_text.index(masked_before)
    if (
        rust_delimiter_stack(masked_text, start) != expected_delimiters
        or (
            not allow_file_inner_attributes
            and rust_file_has_inner_attribute(masked_text, start)
        )
        or rust_item_has_outer_attribute(masked_text, start)
    ):
        message = f"{context}: reviewed item is not an active root item"
        raise RuntimePolicyPatchError(message)
    if text[start : start + len(before)] != before:
        message = f"{context}: reviewed item bytes drifted"
        raise RuntimePolicyPatchError(message)
    return f"{text[:start]}{after}{text[start + len(before) :]}"


def _replace_exact_source_span(
    text: str,
    before: str,
    after: str,
    *,
    context: str,
) -> str:
    """Replace one byte-reviewed non-Rust source span, failing on drift."""
    count = text.count(before)
    if count != 1:
        message = f"{context}: expected exactly 1 reviewed span, found {count}"
        raise RuntimePolicyPatchError(message)
    return text.replace(before, after, 1)


def _patch_buzz_app_source(source: str) -> str:
    if not source.startswith(_BUZZ_APP_INNER_ATTRIBUTE):
        message = "Buzz app recursion-limit attribute drifted"
        raise RuntimePolicyPatchError(message)
    patched = _replace_reviewed_rust_item(
        source,
        _BUZZ_APP_IMPORTS_UPSTREAM,
        _BUZZ_APP_IMPORTS_KEYCHAIN,
        context="Buzz pre-UI Keychain interaction import",
        allow_file_inner_attributes=True,
    )
    patched = _replace_reviewed_rust_item(
        patched,
        _BUZZ_SETUP_UPSTREAM,
        _BUZZ_SETUP_KEYCHAIN_GUARDED,
        context="Buzz pre-UI Keychain interaction scope",
        allow_file_inner_attributes=True,
        expected_delimiters=("{",),
    )
    patched = _replace_reviewed_rust_item(
        patched,
        _BUZZ_BACKFILL_UPSTREAM,
        _BUZZ_BACKFILL_RECOVERY_GUARDED,
        context="Buzz recovery-mode persona backfill",
        allow_file_inner_attributes=True,
        expected_delimiters=("{", "(", "{"),
    )
    patched = _replace_reviewed_rust_item(
        patched,
        _BUZZ_NEST_REGENERATION_UPSTREAM,
        _BUZZ_NEST_REGENERATION_RECOVERY_GUARDED,
        context="Buzz recovery-mode nest regeneration",
        allow_file_inner_attributes=True,
        expected_delimiters=("{", "(", "{"),
    )
    return _replace_reviewed_rust_item(
        patched,
        _BUZZ_IDENTITY_HANDLER_UPSTREAM,
        _BUZZ_IDENTITY_HANDLER_WITH_RETRY,
        context="Buzz post-UI Keychain retry handler",
        allow_file_inner_attributes=True,
        expected_delimiters=("{", "(", "["),
    )


def _patch_buzz_secret_store(source: str) -> str:
    patched = _replace_reviewed_rust_item(
        source,
        _DPK_PROBE_UPSTREAM,
        _DPK_PROBE_FAIL_CLOSED,
        context="Buzz DPK probe failure policy",
        expected_delimiters=("{",),
    )
    patched = _replace_reviewed_rust_item(
        patched,
        _LEGACY_KEYRING_PROBE_UPSTREAM,
        _LEGACY_KEYRING_PROBE_FAIL_CLOSED,
        context="Buzz legacy Keychain probe failure policy",
        expected_delimiters=("{",),
    )
    patched = _replace_reviewed_rust_item(
        patched,
        _KEYCHAIN_LOAD_ALL_READONLY_UPSTREAM,
        _KEYCHAIN_LOAD_ALL_READONLY_WITH_KEY,
        context="Buzz non-mutating legacy identity lookup",
        expected_delimiters=("{",),
    )
    return _replace_reviewed_rust_item(
        patched,
        _KEYRING_STORE_TEST_ANCHOR,
        _KEYRING_STORE_READONLY_TESTS,
        context="Buzz non-mutating identity lookup regression tests",
        expected_delimiters=("{",),
    )


def _patch_keyring_locked_screen(source: str) -> str:
    patched = _replace_exact_source_span(
        source,
        _BUZZ_KEYRING_IMPORT_UPSTREAM,
        _BUZZ_KEYRING_IMPORT_WITH_RETRY,
        context="Buzz Keychain retry UI import",
    )
    patched = _replace_exact_source_span(
        patched,
        _BUZZ_KEYRING_STATE_UPSTREAM,
        _BUZZ_KEYRING_STATE_WITH_RETRY,
        context="Buzz Keychain retry UI state",
    )
    patched = _replace_exact_source_span(
        patched,
        _BUZZ_KEYRING_DESCRIPTION_UPSTREAM,
        _BUZZ_KEYRING_DESCRIPTION_WITH_RETRY,
        context="Buzz Keychain retry UI guidance",
    )
    return _replace_exact_source_span(
        patched,
        _BUZZ_KEYRING_RELAUNCH_UPSTREAM,
        _BUZZ_KEYRING_RELAUNCH_WITH_RETRY,
        context="Buzz Keychain retry UI action",
    )


def patch_buzz_source(root: Path) -> None:
    """Guard Buzz's host-runtime initialization in one copied source tree."""
    app = root / _BUZZ_APP
    app_state = root / _BUZZ_APP_STATE
    app_state_tests = root / _BUZZ_APP_STATE_TESTS
    entrypoint = root / _BUZZ_ENTRYPOINT
    identity_commands = root / _BUZZ_IDENTITY_COMMANDS
    secret_store = root / _BUZZ_SECRET_STORE
    tauri_identity = root / _BUZZ_TAURI_IDENTITY
    keyring_locked_screen = root / _BUZZ_KEYRING_LOCKED_SCREEN
    e2e_bridge = root / _BUZZ_E2E_BRIDGE
    identity_e2e = root / _BUZZ_IDENTITY_E2E
    app_source = app.read_text(encoding="utf-8")
    app_state_source = app_state.read_text(encoding="utf-8")
    app_state_tests_source = app_state_tests.read_text(encoding="utf-8")
    source = entrypoint.read_text(encoding="utf-8")
    identity_commands_source = identity_commands.read_text(encoding="utf-8")
    secret_source = secret_store.read_text(encoding="utf-8")
    tauri_identity_source = tauri_identity.read_text(encoding="utf-8")
    keyring_locked_source = keyring_locked_screen.read_text(encoding="utf-8")
    e2e_bridge_source = e2e_bridge.read_text(encoding="utf-8")
    identity_e2e_source = identity_e2e.read_text(encoding="utf-8")
    patched_app = _patch_buzz_app_source(app_source)
    patched_app_state = _replace_reviewed_rust_item(
        app_state_source,
        _BUZZ_IDENTITY_STORE_IMPL_UPSTREAM,
        _BUZZ_IDENTITY_STORE_IMPL_WITH_RETRY,
        context="Buzz read-only identity resolution and post-UI retry",
    )
    patched_app_state_tests = _replace_reviewed_rust_item(
        app_state_tests_source,
        _BUZZ_APP_STATE_TEST_ANCHOR,
        _BUZZ_APP_STATE_RETRY_TESTS,
        context="Buzz read-only identity retry regression tests",
    )
    patched = _replace_reviewed_rust_item(
        source,
        _BUZZ_STARTUP_ANCHOR,
        _BUZZ_GUARDED_STARTUP,
        context="Buzz Mesh runtime startup anchor",
    )
    patched_identity_commands = _replace_reviewed_rust_item(
        identity_commands_source,
        _BUZZ_DEFAULT_RELAY_COMMAND_UPSTREAM,
        _BUZZ_DEFAULT_RELAY_COMMAND_WITH_RETRY,
        context="Buzz post-UI Keychain retry command",
    )
    patched_secret = _patch_buzz_secret_store(secret_source)
    patched_tauri_identity = _replace_exact_source_span(
        tauri_identity_source,
        _BUZZ_TAURI_GET_NSEC_UPSTREAM,
        _BUZZ_TAURI_GET_NSEC_WITH_RETRY,
        context="Buzz Keychain retry frontend API",
    )
    patched_keyring_locked = _patch_keyring_locked_screen(keyring_locked_source)
    patched_e2e_bridge = _replace_exact_source_span(
        e2e_bridge_source,
        _BUZZ_E2E_GET_IDENTITY_CASE_UPSTREAM,
        _BUZZ_E2E_GET_IDENTITY_CASE_WITH_RETRY,
        context="Buzz Keychain retry E2E bridge",
    )
    patched_identity_e2e = _replace_exact_source_span(
        identity_e2e_source,
        _BUZZ_E2E_RELAUNCH_TEST_UPSTREAM,
        _BUZZ_E2E_RELAUNCH_TEST_WITH_RETRY,
        context="Buzz Keychain retry E2E regression",
    )
    # Write only after every reviewed transform succeeds so a drift failure
    # cannot leave a partially patched source tree.
    app.write_text(patched_app, encoding="utf-8")
    app_state.write_text(patched_app_state, encoding="utf-8")
    app_state_tests.write_text(patched_app_state_tests, encoding="utf-8")
    entrypoint.write_text(patched, encoding="utf-8")
    identity_commands.write_text(patched_identity_commands, encoding="utf-8")
    secret_store.write_text(patched_secret, encoding="utf-8")
    tauri_identity.write_text(patched_tauri_identity, encoding="utf-8")
    keyring_locked_screen.write_text(patched_keyring_locked, encoding="utf-8")
    e2e_bridge.write_text(patched_e2e_bridge, encoding="utf-8")
    identity_e2e.write_text(patched_identity_e2e, encoding="utf-8")


def patch_desktop_cargo_deps(root: Path) -> None:
    """Disable Mesh network policy and unused Sherpa TTS links in copied vendor."""
    mesh_matches = sorted(root.glob(_MESH_RUNTIME_INSTALL_GLOB))
    if len(mesh_matches) != 1:
        message = (
            "desktop Cargo vendor must contain exactly one "
            "mesh-llm-runtime-install-0.75.1/src/lib.rs; "
            f"found {len(mesh_matches)}"
        )
        raise RuntimePolicyPatchError(message)

    sherpa_matches = sorted(root.glob(_SHERPA_ONNX_SYS_BUILD_GLOB))
    if len(sherpa_matches) != 1:
        message = (
            "desktop Cargo vendor must contain exactly one "
            "sherpa-onnx-sys-1.13.4/build.rs; "
            f"found {len(sherpa_matches)}"
        )
        raise RuntimePolicyPatchError(message)

    mesh_path = mesh_matches[0]
    mesh_source = mesh_path.read_text(encoding="utf-8")
    patched_mesh = _replace_reviewed_rust_item(
        mesh_source,
        _MANIFEST_DEFAULT_UPSTREAM,
        _MANIFEST_DEFAULT_PATCHED,
        context="ManifestOptions allow_default_manifest_url default",
    )
    patched_mesh = _replace_reviewed_rust_item(
        patched_mesh,
        _INSTALL_DEFAULT_UPSTREAM,
        _INSTALL_DEFAULT_PATCHED,
        context="InstallOptions allow_download default",
    )
    patched_mesh = _replace_reviewed_rust_item(
        patched_mesh,
        _INSTALL_NATIVE_RUNTIME_UPSTREAM,
        _INSTALL_NATIVE_RUNTIME_PATCHED,
        context="install_native_runtime allow_default_manifest_url hardcode",
    )

    sherpa_path = sherpa_matches[0]
    sherpa_source = sherpa_path.read_text(encoding="utf-8")
    patched_sherpa = _replace_reviewed_rust_item(
        sherpa_source,
        _SHERPA_STATIC_LIBS_UPSTREAM,
        _SHERPA_STATIC_LIBS_TTS_OFF,
        context="sherpa-onnx-sys static link list",
    )

    mesh_path.write_text(patched_mesh, encoding="utf-8")
    sherpa_path.write_text(patched_sherpa, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    """Patch one copied source or desktop Cargo vendor tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("buzz-source", "desktop-cargo-deps"),
        help="Copied tree type to patch.",
    )
    parser.add_argument("root", type=Path, help="Copied tree root to patch.")
    args = parser.parse_args(argv)
    if args.mode == "buzz-source":
        patch_buzz_source(args.root)
    else:
        patch_desktop_cargo_deps(args.root)


if __name__ == "__main__":  # pragma: no cover -- CLI entrypoint
    main()
