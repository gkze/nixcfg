{
  abseil-cpp_202601,
  cctools,
  fetchFromGitHub,
  fetchPnpmDeps,
  fetchurl,
  inputs,
  ld64,
  lib,
  nativeLock ? builtins.fromJSON (builtins.readFile ./native-lock.json),
  nodejs_24,
  onnxruntime,
  outputs,
  pkgs,
  pnpm_11,
  protobuf,
  python3,
  rustPlatform,
  sherpa-onnx,
  stdenv,
  stdenvNoCC,
  ...
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  pname = "buzz";
  expectedMacApp = {
    bundleId = "xyz.block.buzz.app";
    bundleName = "Buzz.app";
    bundleRelPath = "Applications/Buzz.app";
    installMode = "copy";
  };

  buzzLock = nativeLock.buzz or { };
  pnpmLock = nativeLock.pnpm or { };
  sherpaOnnxClosure = nativeLock.sherpaOnnx or { };
  onnxRuntimeClosure = nativeLock.onnxruntime or { };
  meshLlmClosure = nativeLock.meshLlm or { };
  llamaCppClosure = nativeLock.llamaCpp or { };
  expectedVersion = buzzLock.version or "";
  expectedCommit = buzzLock.commit or "";
  expectedPnpmVersion = pnpmLock.version or "";
  expectedRustVersion = buzzLock.rustVersion or "";
  expectedSherpaOnnxVersion = sherpaOnnxClosure.version or "";
  expectedSherpaOnnxCommit = sherpaOnnxClosure.commit or "";
  expectedOnnxRuntimeVersion = onnxRuntimeClosure.version or "";
  expectedOnnxRuntimeCommit = onnxRuntimeClosure.commit or "";
  expectedMeshLlmVersion = meshLlmClosure.version or "";
  expectedMeshLlmCommit = meshLlmClosure.commit or "";
  expectedSkippyAbi = meshLlmClosure.skippyAbi or "";
  expectedLlamaCppCommit = llamaCppClosure.commit or "";

  nonEmptyString = value: builtins.isString value && value != "";
  commitString = value: nonEmptyString value && builtins.match "[0-9a-f]{40}" value != null;
  completeNativeFetch =
    dependency:
    nonEmptyString (dependency.cmakeVariable or null)
    && nonEmptyString (dependency.file or null)
    && nonEmptyString (dependency.url or null)
    && builtins.isString (dependency.hash or null)
    && lib.hasPrefix "sha256-" dependency.hash;
  sherpaDependencies = sherpaOnnxClosure.dependencies or { };
  sherpaDependencyOrder = sherpaOnnxClosure.dependencyOrder or [ ];
  nativeLockComplete =
    (nativeLock.schemaVersion or null) == 1
    && nonEmptyString (buzzLock.version or null)
    && commitString (buzzLock.commit or null)
    && nonEmptyString (buzzLock.rustVersion or null)
    && nonEmptyString (pnpmLock.version or null)
    && nonEmptyString (pnpmLock.url or null)
    && builtins.isString (pnpmLock.hash or null)
    && lib.hasPrefix "sha256-" pnpmLock.hash
    && nonEmptyString (sherpaOnnxClosure.version or null)
    && commitString (sherpaOnnxClosure.commit or null)
    && builtins.length (builtins.attrValues sherpaDependencies) == 6
    && builtins.all completeNativeFetch (builtins.attrValues sherpaDependencies)
    && builtins.isList sherpaDependencyOrder
    && builtins.length sherpaDependencyOrder == 6
    && builtins.all nonEmptyString sherpaDependencyOrder
    && builtins.sort builtins.lessThan sherpaDependencyOrder == builtins.attrNames sherpaDependencies
    && nonEmptyString (onnxRuntimeClosure.version or null)
    && commitString (onnxRuntimeClosure.commit or null)
    && nonEmptyString (meshLlmClosure.version or null)
    && commitString (meshLlmClosure.commit or null)
    && nonEmptyString (meshLlmClosure.skippyAbi or null)
    && commitString (llamaCppClosure.commit or null);

  source = outputs.lib.sourceEntry pname;
  inherit (source) version;

  hashEntryFor =
    hashType: url:
    lib.findFirst (entry: entry.hashType == hashType && (entry.url or null) == url) null source.hashes;
  srcHashEntry = hashEntryFor "srcHash" source.urls.buzz;
  onnxRuntimeSrcHashEntry = hashEntryFor "srcHash" source.urls.onnxruntime;
  sherpaOnnxSrcHashEntry =
    if source.urls ? sherpaOnnx then hashEntryFor "srcHash" source.urls.sherpaOnnx else null;
  meshLlmSrcHashEntry =
    if source.urls ? meshLlm then hashEntryFor "srcHash" source.urls.meshLlm else null;
  llamaCppSrcHashEntry =
    if source.urls ? llamaCpp then hashEntryFor "srcHash" source.urls.llamaCpp else null;
  npmDepsHashEntry = hashEntryFor "npmDepsHash" source.urls.buzz;
  rootCargoHashEntry = hashEntryFor "vendorHash" source.urls.buzz;
  desktopCargoHashEntry = hashEntryFor "cargoHash" source.urls.buzz;
  hashOrFake = entry: if entry == null then lib.fakeHash else entry.hash;

  # Build the exact rust-overlay interface from the package set supplied by the
  # caller. Internal updater probes intentionally apply only this repository's
  # overlay, so reaching through pkgs.rust-bin would make those probes depend on
  # an ambient overlay that is not part of the package function's arguments.
  rustBin = inputs.rust-overlay.lib.mkRustBin { } pkgs;

  src = fetchFromGitHub {
    owner = "block";
    repo = "buzz";
    rev = source.commit;
    fetchSubmodules = false;
    hash = hashOrFake srcHashEntry;
  };

  pnpm = (pnpm_11.override { nodejs-slim = nodejs_24; }).overrideAttrs (_: {
    version = expectedPnpmVersion;
    src = fetchurl {
      inherit (pnpmLock) url hash;
    };
  });
  pnpmDeps =
    let
      args = {
        inherit
          pname
          pnpm
          src
          version
          ;
        fetcherVersion = 4;
        hash = hashOrFake npmDepsHashEntry;
      };
    in
    fetchPnpmDeps args;

  # Buzz intentionally carries two independent Cargo.lock files. The root lock
  # owns the six sidecars; desktop/src-tauri/Cargo.lock owns the Tauri binary.
  # These FODs exist only so the manual updater can discover each closure hash.
  rootCargoDeps = rustPlatform.fetchCargoVendor {
    inherit src;
    name = "${pname}-${version}-root-cargo-vendor";
    cargoRoot = ".";
    hash = hashOrFake rootCargoHashEntry;
  };
  desktopCargoDeps = rustPlatform.fetchCargoVendor {
    inherit src;
    name = "${pname}-${version}-desktop-cargo-vendor";
    cargoRoot = "desktop/src-tauri";
    hash = hashOrFake desktopCargoHashEntry;
  };

  sidecarSpecs = [
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

  # These contracts describe the derivations that must eventually live under
  # packages/buzz/native/. They are deliberately not function arguments: a
  # caller cannot pass a path or a provenance string and attest its own bytes.
  expectedNativeContracts = {
    rustToolchain = {
      kind = "rust-toolchain";
      channel = expectedRustVersion;
      profile = "default";
      target = "aarch64-apple-darwin";
    };
    sidecars = {
      kind = "buzz-sidecars";
      commit = expectedCommit;
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
    onnxRuntime = {
      kind = "onnxruntime";
      version = expectedOnnxRuntimeVersion;
      commit = expectedOnnxRuntimeCommit;
      target = "aarch64-apple-darwin";
      configuration = "Release";
      assemblyBuildSharedLib = true;
      assemblyBuildAppleFramework = true;
      deliveredSharedLib = false;
      deliveredAppleFramework = false;
      skipTests = true;
      monolithicStaticArchive = true;
    };
    sherpaOnnx = {
      kind = "sherpa-onnx";
      version = expectedSherpaOnnxVersion;
      commit = expectedSherpaOnnxCommit;
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
    meshLlm = {
      kind = "mesh-llm";
      version = expectedMeshLlmVersion;
      commit = expectedMeshLlmCommit;
      sdkFeatures = [
        "client"
        "serving"
      ];
      hostRuntimeFeatures = [ "dynamic-native-runtime" ];
    };
    llamaCpp = {
      kind = "llama.cpp";
      commit = expectedLlamaCppCommit;
      target = "aarch64-apple-darwin";
      backend = "metal";
      linkMode = "dynamic";
      buildType = "Release";
      ggmlNative = false;
      cmakeOptions = {
        BUILD_SHARED_LIBS = true;
        GGML_METAL = true;
        LLAMA_BUILD_APP = false;
        LLAMA_BUILD_EXAMPLES = false;
        LLAMA_BUILD_SERVER = false;
        LLAMA_BUILD_TESTS = false;
        LLAMA_CURL = false;
        LLAMA_OPENSSL = false;
      };
    };
    meshRuntimeBundle = {
      kind = "mesh-native-runtime-bundle";
      meshVersion = expectedMeshLlmVersion;
      skippyAbi = expectedSkippyAbi;
      target = "aarch64-apple-darwin";
      platform = {
        os = "macos";
        arch = "aarch64";
      };
      backend = "metal";
      sourceInputs = [
        "meshLlm"
        "llamaCpp"
      ];
      manifestHasFileDigests = true;
      releaseArchiveAllowed = false;
    };
    patchedBuzzSource = {
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
    };
  };

  # Every slot is repo-owned. Wiring a foundation requires a concrete
  # derivation in this file (or a package-local import), plus exact
  # passthru.buzzNativeContract metadata.
  rustToolchainNative = import ./native/rust-toolchain.nix {
    inherit
      lib
      nativeLock
      rustBin
      stdenv
      ;
  };
  onnxRuntimeNative =
    if onnxRuntimeSrcHashEntry == null then
      null
    else
      import ./native/onnxruntime.nix {
        inherit
          abseil-cpp_202601
          cctools
          fetchFromGitHub
          ld64
          lib
          nativeLock
          onnxruntime
          protobuf
          python3
          stdenv
          ;
        srcHash = onnxRuntimeSrcHashEntry.hash;
      };
  sherpaOnnxNative =
    if sherpaOnnxSrcHashEntry == null || onnxRuntimeNative == null || !nativeLockComplete then
      null
    else
      import ./native/sherpa-onnx.nix {
        inherit
          cctools
          fetchFromGitHub
          fetchurl
          lib
          sherpa-onnx
          stdenv
          ;
        inherit (pkgs)
          eigen_5
          libarchive
          nlohmann_json
          ;
        onnxRuntime = onnxRuntimeNative;
        closureContract = sherpaOnnxClosure;
        srcHash = sherpaOnnxSrcHashEntry.hash;
      };
  meshLlmNative =
    if meshLlmSrcHashEntry == null then
      null
    else
      import ./native/mesh-llm.nix {
        inherit
          fetchFromGitHub
          lib
          nativeLock
          python3
          stdenvNoCC
          ;
        srcHash = meshLlmSrcHashEntry.hash;
      };
  llamaCppNative =
    if llamaCppSrcHashEntry == null || meshLlmSrcHashEntry == null then
      null
    else
      import ./native/llama-cpp.nix {
        inherit
          cctools
          fetchFromGitHub
          lib
          nativeLock
          stdenv
          ;
        inherit (pkgs) cmake gitMinimal ninja;
        meshSrcHash = meshLlmSrcHashEntry.hash;
        srcHash = llamaCppSrcHashEntry.hash;
      };
  meshRuntimeBundleNative =
    if meshLlmSrcHashEntry == null || llamaCppSrcHashEntry == null then
      null
    else
      import ./native/mesh-runtime-bundle.nix {
        inherit
          cctools
          fetchFromGitHub
          lib
          nativeLock
          python3
          stdenv
          stdenvNoCC
          ;
        inherit (pkgs) cmake gitMinimal ninja;
        meshLlmSrcHash = meshLlmSrcHashEntry.hash;
        llamaCppSrcHash = llamaCppSrcHashEntry.hash;
      };
  buzzRuntimePolicySource = import ./native/buzz-runtime-policy.nix {
    inherit
      desktopCargoDeps
      nativeLock
      python3
      src
      stdenvNoCC
      version
      ;
    expectedContract = expectedNativeContracts.patchedBuzzSource;
  };
  sidecarsNative = import ./native/sidecars.nix {
    inherit
      cctools
      lib
      nativeLock
      rootCargoDeps
      sidecarSpecs
      stdenv
      version
      ;
    inherit (pkgs) makeRustPlatform;
    patchedBuzzSource = buzzRuntimePolicySource;
    rustToolchain = rustToolchainNative;
  };
  desktopUnsignedNative =
    if sherpaOnnxNative == null then
      null
    else
      import ./native/desktop.nix {
        inherit
          lib
          nativeLock
          nodejs_24
          pnpm
          pnpmDeps
          stdenv
          version
          ;
        inherit (pkgs)
          cargo-tauri
          cmake
          makeRustPlatform
          pkg-config
          pnpmConfigHook
          ;
        patchedBuzzSource = buzzRuntimePolicySource;
        patchedDesktopCargoDeps = buzzRuntimePolicySource.passthru.desktopCargoDeps;
        rustToolchain = rustToolchainNative;
        sherpaOnnx = sherpaOnnxNative;
        sidecars = sidecarsNative;
      };
  desktopCandidateNative =
    if desktopUnsignedNative == null || meshRuntimeBundleNative == null then
      null
    else
      import ./native/desktop-candidate.nix {
        inherit
          cctools
          lib
          nativeLock
          python3
          stdenv
          version
          ;
        desktopUnsigned = desktopUnsignedNative;
        meshRuntimeBundle = meshRuntimeBundleNative;
        patchedBuzzSource = buzzRuntimePolicySource;
      };
  desktopCandidateWired = lib.isDerivation desktopCandidateNative;
  # This evidence records the exact candidate whose real artifact, isolated
  # launcher, offline runtime, signatures, app metadata, and reference closure
  # were validated. Its store identities remain independent of the candidate
  # being evaluated, but the updater owns them alongside every other native
  # lock so this derivation never embeds an immutable identity directly.
  desktopBundleValidationEvidence = nativeLock.desktopBundleValidation or { };
  desktopCandidateIdentity =
    if desktopCandidateWired then
      {
        derivationPath = builtins.unsafeDiscardStringContext desktopCandidateNative.drvPath;
        outputPath = builtins.unsafeDiscardStringContext desktopCandidateNative.outPath;
      }
    else
      null;
  expectedDesktopBundleValidation = {
    schemaVersion = 1;
    status = "passed";
    candidate = desktopCandidateIdentity;
    checks = [
      "realized-candidate"
      "isolated-launcher-startup"
      "offline-runtime-loading"
      "signatures"
      "exact-app-metadata"
      "reference-free-final-bundle"
    ];
  };
  desktopBundleValidationComplete =
    desktopCandidateWired && desktopBundleValidationEvidence == expectedDesktopBundleValidation;
  desktopCandidateExportReady =
    desktopCandidateWired
    && desktopBundleValidationComplete
    && (desktopCandidateNative.passthru.buzzNativeContract.exportReady or false)
    && (desktopCandidateNative.passthru.macApp or null) == expectedMacApp;
  desktopExportGate = lib.optional (!desktopCandidateExportReady) ''
    Buzz desktop export is disabled. Realization, isolated launcher startup,
    offline runtime loading, signatures, exact app metadata, and a
    reference-free final bundle must pass before app routing can be enabled.
  '';
  nativeFoundationSlots = {
    rustToolchain = rustToolchainNative;
    sidecars = sidecarsNative;
    onnxRuntime = onnxRuntimeNative;
    sherpaOnnx = sherpaOnnxNative;
    meshLlm = meshLlmNative;
    llamaCpp = llamaCppNative;
    meshRuntimeBundle = meshRuntimeBundleNative;
    patchedBuzzSource = buzzRuntimePolicySource;
  };
  nativeFoundationNames = builtins.attrNames expectedNativeContracts;
  slotMatches =
    name:
    let
      candidate = nativeFoundationSlots.${name};
    in
    lib.isDerivation candidate
    && (candidate.passthru.buzzNativeContract or null) == expectedNativeContracts.${name};
  nativeFoundationReady = builtins.all slotMatches nativeFoundationNames;
  missingNativeFoundation = builtins.filter (name: !(slotMatches name)) nativeFoundationNames;

  unresolvedBuildGates =
    lib.optional (!nativeLockComplete) "Buzz updater-owned native lock is incomplete"
    ++ lib.optional (version != expectedVersion) "Buzz source version must be ${expectedVersion}"
    ++ lib.optional (source.commit != expectedCommit) "Buzz source commit must be ${expectedCommit}"
    ++ lib.optional (pnpm.version != expectedPnpmVersion) "pnpm must be exactly ${expectedPnpmVersion}"
    ++ lib.optional (srcHashEntry == null) "Buzz srcHash is missing"
    ++ lib.optional (
      onnxRuntimeSrcHashEntry == null
    ) "ONNX Runtime ${expectedOnnxRuntimeVersion} srcHash is missing"
    ++ lib.optional (
      sherpaOnnxSrcHashEntry == null
    ) "sherpa-onnx ${expectedSherpaOnnxVersion} srcHash is missing"
    ++ lib.optional (
      meshLlmSrcHashEntry == null
    ) "Mesh-LLM ${expectedMeshLlmVersion} srcHash is missing"
    ++ lib.optional (
      llamaCppSrcHashEntry == null
    ) "llama.cpp ${expectedLlamaCppCommit} srcHash is missing"
    ++ lib.optional (npmDepsHashEntry == null) "Buzz pnpm npmDepsHash is unresolved"
    ++ lib.optional (rootCargoHashEntry == null) "Buzz root Cargo vendorHash is unresolved"
    ++ lib.optional (desktopCargoHashEntry == null) "Buzz desktop Cargo cargoHash is unresolved"
    ++ lib.optional (!nativeFoundationReady) ''
      Buzz's source-only native foundation is unresolved: ${builtins.concatStringsSep ", " missingNativeFoundation}. The selected Mesh feature graph bypasses LLAMA_STAGE_BUILD_DIR and the
      upstream first-use installer permits network downloads with checksum-only
      verification. A repo-built Mesh ${expectedMeshLlmVersion} / Skippy ABI
      ${expectedSkippyAbi} Metal runtime bundle and a source patch that forces
      allow_download=false and disables the default manifest URL are mandatory.
      The sherpa-onnx-sys precompiled static archives cannot satisfy any slot.
    ''
    ++ desktopExportGate;

  commonPassthru = {
    inherit
      expectedNativeContracts
      llamaCppSrcHashEntry
      meshLlmSrcHashEntry
      nativeFoundationSlots
      pnpmDeps
      rootCargoDeps
      sidecarSpecs
      ;
    sidecars = sidecarsNative;
    macApp = expectedMacApp;
    desktopUnsigned = desktopUnsignedNative;
    desktopCandidate = desktopCandidateNative;
    desktopCargoDeps = buzzRuntimePolicySource.passthru.desktopCargoDeps;
    buzzDesktopCandidateStatus = {
      wired = desktopCandidateWired;
      identity = desktopCandidateIdentity;
      evidence = desktopBundleValidationEvidence;
      validationComplete = desktopBundleValidationComplete;
      exportReady = desktopCandidateExportReady;
    };
    buzzBuildGates = unresolvedBuildGates;
    buzzNativeFoundationStatus = {
      ready = nativeFoundationReady;
      missingDerivations = missingNativeFoundation;
    };
    buzzNativeBuildPlan = {
      cargoLocks = [
        "Cargo.lock"
        "desktop/src-tauri/Cargo.lock"
      ];
      sidecars = sidecarSpecs;
      tauri = {
        workingDirectory = "desktop";
        cargoRoot = "desktop/src-tauri";
        feature = "mesh-llm";
        bundles = [ "app" ];
      };
      nativeRuntime = {
        currentBehavior = {
          dynamicNativeRuntime = true;
          llamaStageBuildDirEffective = false;
          firstUseDownloadAllowed = true;
          verification = "checksum-only";
          signatureVerificationImplemented = false;
        };
        requiredReplacement = {
          bundle = "repo-owned meshRuntimeBundle derivation";
          discoveryEnvironment = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
          sourcePatch = "repo-owned patchedBuzzSource derivation";
          launchEnvironment = buzzRuntimePolicySource.passthru.requiredLaunchEnvironment;
          allowDefaultManifestUrl = false;
          allowDownload = false;
        };
      };
      updaterEnvironment = {
        BUZZ_UPDATER_ENDPOINT = "";
        BUZZ_UPDATER_PUBLIC_KEY = "";
      };
    };
  };
  validatedPackage = desktopCandidateNative.overrideAttrs (old: {
    passthru = (old.passthru or { }) // commonPassthru;
  });
  blockedPackage = stdenvNoCC.mkDerivation {
    inherit pname version;
    dontUnpack = true;
    buildPhase = ''
      echo "Buzz is intentionally unbuildable:" >&2
      ${lib.concatMapStringsSep "\n" (
        gate: "echo ${lib.escapeShellArg "- ${gate}"} >&2"
      ) unresolvedBuildGates}
      exit 1
    '';
    installPhase = "exit 1";
    passthru = commonPassthru;
    meta = {
      broken = true;
      description = "Unexported source-first foundation for the Buzz desktop app";
      homepage = "https://github.com/block/buzz";
      license = lib.licenses.asl20;
      platforms = [ "aarch64-darwin" ];
    };
  };
in
if unresolvedBuildGates == [ ] then validatedPackage else blockedPackage
