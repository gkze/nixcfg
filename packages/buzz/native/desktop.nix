{
  cargo-tauri,
  cmake,
  lib,
  makeRustPlatform,
  nodejs_24,
  patchedBuzzSource,
  patchedDesktopCargoDeps,
  pkg-config,
  pnpm,
  pnpmConfigHook,
  pnpmDeps,
  rustToolchain,
  sherpaOnnx,
  sidecars,
  stdenv,
  version,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  expectedRustContract = {
    kind = "rust-toolchain";
    channel = "1.95.0";
    profile = "default";
    target = "aarch64-apple-darwin";
  };
  expectedSourceContract = {
    kind = "buzz-runtime-policy-source";
    commit = "95154bee4034ca7a40b33095c2ddbde8c9aa1614";
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
  expectedSherpaContract = {
    kind = "sherpa-onnx";
    version = "1.13.4";
    commit = "142807252687d81b40d6315f23470a1512a00de3";
    target = "aarch64-apple-darwin";
    linkMode = "static";
    usePreinstalledOnnxRuntime = true;
    precompiledReleaseArchivesAllowed = false;
    cmakeOptions = {
      BUILD_SHARED_LIBS = false;
      SHERPA_ONNX_ENABLE_BINARY = false;
      SHERPA_ONNX_ENABLE_C_API = true;
      SHERPA_ONNX_ENABLE_GPU = false;
      SHERPA_ONNX_ENABLE_TESTS = false;
      SHERPA_ONNX_ENABLE_TTS = false;
    };
  };
  expectedSidecarsContract = {
    kind = "buzz-sidecars";
    commit = "95154bee4034ca7a40b33095c2ddbde8c9aa1614";
    target = "aarch64-apple-darwin";
    profile = "release";
    cargoOffline = true;
    cargoFrozen = true;
    sidecars = [
      {
        package = "buzz-acp";
        binary = "buzz-acp";
      }
      {
        package = "buzz-agent";
        binary = "buzz-agent";
      }
      {
        package = "buzz-backend-kubernetes";
        binary = "buzz-backend-kubernetes";
      }
      {
        package = "buzz-dev-mcp";
        binary = "buzz-dev-mcp";
      }
      {
        package = "git-credential-nostr";
        binary = "git-credential-nostr";
      }
      {
        package = "buzz-cli";
        binary = "buzz";
      }
    ];
    installedBinaries = [
      "buzz-acp-aarch64-apple-darwin"
      "buzz-agent-aarch64-apple-darwin"
      "buzz-backend-kubernetes-aarch64-apple-darwin"
      "buzz-dev-mcp-aarch64-apple-darwin"
      "git-credential-nostr-aarch64-apple-darwin"
      "buzz-aarch64-apple-darwin"
    ];
    binaryFormat = "Mach-O 64-bit executable arm64";
    dylibPolicy = "system-or-loader-relative";
    signature = "adhoc-after-fixup";
  };
  implementedContract = {
    kind = "buzz-desktop-unsigned";
    commit = "95154bee4034ca7a40b33095c2ddbde8c9aa1614";
    version = "0.5.20";
    target = "aarch64-apple-darwin";
    rustVersion = "1.95.0";
    pnpmVersion = "11.4.0";
    cargoRoot = "desktop/src-tauri";
    buildAndTestSubdir = "desktop";
    cargoOffline = true;
    cargoFrozen = true;
    frontendBuildCommand = "pnpm build";
    cargoFeatures = [ "mesh-llm" ];
    sidecars = [
      "buzz-acp-aarch64-apple-darwin"
      "buzz-agent-aarch64-apple-darwin"
      "buzz-backend-kubernetes-aarch64-apple-darwin"
      "buzz-dev-mcp-aarch64-apple-darwin"
      "git-credential-nostr-aarch64-apple-darwin"
      "buzz-aarch64-apple-darwin"
    ];
    updaterEnabled = false;
    sherpaOnnxVersion = "1.13.4";
    minimumMacosVersion = "14.0";
    appSigned = false;
    runtimeBundleEmbedded = false;
  };
  desktopRustPlatform = makeRustPlatform {
    cargo = rustToolchain;
    rustc = rustToolchain;
  };
  stagingScript = ''
    sidecarSource="${sidecars}/bin"
    # The pinned cargo-tauri hook invokes preBuild before entering
    # buildAndTestSubdir, so PWD is the unpacked source root here.
    sidecarDestination="$PWD/desktop/src-tauri/binaries"
    expectedInventory="$TMPDIR/buzz-desktop-sidecars.expected"
    actualInventory="$TMPDIR/buzz-desktop-sidecars.actual"
    unsortedInventory="$TMPDIR/buzz-desktop-sidecars.unsorted"
    unsupportedInventory="$TMPDIR/buzz-desktop-sidecars.unsupported"

    if [ ! -d "$sidecarSource" ] || [ -L "$sidecarSource" ]; then
      echo "Buzz sidecar source is not a regular directory" >&2
      exit 1
    fi
    if [ -e "$sidecarDestination" ] || [ -L "$sidecarDestination" ]; then
      echo "Buzz sidecar destination already exists" >&2
      exit 1
    fi

    printf '%s\n' \
      'buzz-aarch64-apple-darwin' \
      'buzz-acp-aarch64-apple-darwin' \
      'buzz-agent-aarch64-apple-darwin' \
      'buzz-backend-kubernetes-aarch64-apple-darwin' \
      'buzz-dev-mcp-aarch64-apple-darwin' \
      'git-credential-nostr-aarch64-apple-darwin' > "$expectedInventory"
    if ! find "$sidecarSource" -mindepth 1 -maxdepth 1 ! -type f \
      -print -quit > "$unsupportedInventory"
    then
      echo "Buzz sidecar source inventory could not be inspected" >&2
      exit 1
    fi
    if [ -s "$unsupportedInventory" ]; then
      echo "Buzz sidecar source inventory is not exact" >&2
      exit 1
    fi
    if ! find "$sidecarSource" -mindepth 1 -maxdepth 1 -type f \
      -exec basename {} \; > "$unsortedInventory"
    then
      echo "Buzz sidecar source inventory could not be inspected" >&2
      exit 1
    fi
    if ! LC_ALL=C sort "$unsortedInventory" > "$actualInventory"; then
      echo "Buzz sidecar source inventory could not be sorted" >&2
      exit 1
    fi
    if ! cmp -s "$expectedInventory" "$actualInventory"; then
      echo "Buzz sidecar source inventory is not exact" >&2
      exit 1
    fi

    mkdir -p "$sidecarDestination"
    while IFS= read -r sidecarName; do
      if [ ! -x "$sidecarSource/$sidecarName" ]; then
        echo "Buzz sidecar is not executable: $sidecarName" >&2
        exit 1
      fi
      install -m0755 \
        "$sidecarSource/$sidecarName" \
        "$sidecarDestination/$sidecarName"
    done < "$expectedInventory"
  '';
in
assert version == "0.5.20";
assert pnpm.version == "11.4.0";
assert lib.isDerivation pnpmDeps;
assert (rustToolchain.passthru.buzzNativeContract or null) == expectedRustContract;
assert (patchedBuzzSource.passthru.buzzNativeContract or null) == expectedSourceContract;
assert (patchedBuzzSource.passthru.desktopCargoDeps or null) == patchedDesktopCargoDeps;
assert (sherpaOnnx.passthru.buzzNativeContract or null) == expectedSherpaContract;
assert (sidecars.passthru.buzzNativeContract or null) == expectedSidecarsContract;
desktopRustPlatform.buildRustPackage {
  pname = "buzz-desktop-unsigned";
  inherit version;
  src = patchedBuzzSource;
  cargoDeps = patchedDesktopCargoDeps;
  inherit pnpmDeps;
  cargoRoot = "desktop/src-tauri";
  buildAndTestSubdir = "desktop";
  strictDeps = true;

  nativeBuildInputs = [
    cargo-tauri.hook
    cmake
    nodejs_24
    pkg-config
    pnpm
    pnpmConfigHook
  ];
  buildInputs = [ sherpaOnnx ];

  env = {
    CARGO_NET_OFFLINE = "true";
    CI = "true";
    MACOSX_DEPLOYMENT_TARGET = "14.0";
    CMAKE_OSX_DEPLOYMENT_TARGET = "14.0";
    npm_config_manage_package_manager_versions = "false";
    SHERPA_ONNX_LIB_DIR = "${lib.getLib sherpaOnnx}/lib";
    BUZZ_UPDATER_ENDPOINT = "";
    BUZZ_UPDATER_PUBLIC_KEY = "";
  };

  cargoBuildFlags = [ "--frozen" ];
  cargoBuildFeatures = [ "mesh-llm" ];
  tauriBundleType = "app";
  tauriBuildFlags = [
    "--no-sign"
    "--verbose"
  ];
  doCheck = false;

  # cargo-tauri.hook propagates the Cargo used to build its CLI. Keep Buzz's
  # audited Rust 1.95 toolchain first for the hook's later `cargo tauri` call.
  preBuild = ''
    export PATH="${rustToolchain}/bin:$PWD/node_modules/.bin:$PATH"
    ${stagingScript}
  '';

  passthru.buzzNativeContract = implementedContract;

  meta = {
    description = "Unsigned source-built Buzz desktop app candidate";
    platforms = [ "aarch64-darwin" ];
  };
}
