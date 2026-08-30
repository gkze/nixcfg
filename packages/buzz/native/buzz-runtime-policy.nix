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
