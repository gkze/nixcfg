{
  cctools,
  desktopUnsigned,
  lib,
  meshRuntimeBundle,
  nativeLock ? builtins.fromJSON (builtins.readFile ../native-lock.json),
  patchedBuzzSource,
  python3,
  stdenv,
  version,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  inspectionPython = python3.withPackages (ps: [ ps.macholib ]);
  inspectionSource = lib.fileset.toSource {
    root = ../../..;
    fileset = lib.fileset.unions [
      ../../../lib/__init__.py
      ../../../lib/macho.py
    ];
  };
  buzzLock = nativeLock.buzz or { };
  pnpmLock = nativeLock.pnpm or { };
  sherpaLock = nativeLock.sherpaOnnx or { };
  meshLlmLock = nativeLock.meshLlm or { };
  buzzCommit = buzzLock.commit or null;
  buzzVersion = buzzLock.version or null;
  rustVersion = buzzLock.rustVersion or null;
  pnpmVersion = pnpmLock.version or null;
  sherpaVersion = sherpaLock.version or null;
  meshLlmVersion = meshLlmLock.version or null;
  skippyAbi = meshLlmLock.skippyAbi or null;
  expectedDesktopContract = {
    kind = "buzz-desktop-unsigned";
    commit = buzzCommit;
    version = buzzVersion;
    target = "aarch64-apple-darwin";
    inherit rustVersion pnpmVersion;
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
    sherpaOnnxVersion = sherpaVersion;
    minimumMacosVersion = "14.0";
    appSigned = false;
    runtimeBundleEmbedded = false;
  };
  expectedRuntimeContract = {
    kind = "mesh-native-runtime-bundle";
    meshVersion = meshLlmVersion;
    inherit skippyAbi;
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
  expectedSourceContract = {
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
  implementedContract = {
    kind = "buzz-desktop-candidate";
    commit = buzzCommit;
    version = buzzVersion;
    target = "aarch64-apple-darwin";
    minimumMacosVersion = "14.0";
    app = {
      bundleName = "Buzz.app";
      identifier = "xyz.block.buzz.app";
      launcherExecutable = "buzz-desktop";
      payloadExecutable = "buzz-desktop.real";
      sidecars = [
        "buzz-acp"
        "buzz-agent"
        "buzz-backend-kubernetes"
        "buzz-dev-mcp"
        "git-credential-nostr"
        "buzz"
      ];
    };
    launcher = {
      language = "c11";
      source = "buzz-launcher.c";
      handoff = "execv";
      runtimeBundleSubpath = "Contents/Resources/mesh-runtime";
      runtimeCacheSubpath = "Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes";
      runtimeBundleEnvironment = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
      runtimeCacheEnvironment = "MESH_LLM_NATIVE_RUNTIME_CACHE_DIR";
      manifestUrlEnvironment = "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL";
      manifestUrlUnset = true;
      createsCacheDirectory = false;
      installCheckSmoke = true;
    };
    signing = {
      identity = "adhoc";
      deepSign = false;
      runtimeResigned = false;
      entitlementsSource = "patched-buzz-source";
    };
    appSigned = true;
    runtimeBundleEmbedded = true;
    exportReady = true;
  };
  runtimeValidationCommand = "\"$PYTHON_TOOL\" ${./validate_runtime.py} ${lib.escapeShellArg meshLlmVersion} ${lib.escapeShellArg skippyAbi}";
  runtimeLoadValidationCommand = "\"$PYTHON_TOOL\" ${./validate_runtime_load.py} ${lib.escapeShellArg skippyAbi}";
  entitlementsValidationCommand = "\"$PYTHON_TOOL\" ${./validate_entitlements.py}";
  rpathValidationCommand = "PYTHONPATH=${inspectionSource} ${inspectionPython}/bin/python3 ${./validate_rpaths.py}";
  assemblyScript = ''
    set -o pipefail

    sourceApp="$DESKTOP_UNSIGNED/Applications/Buzz.app"
    app="$out/Applications/Buzz.app"
    macos="$app/Contents/MacOS"
    launcher="$macos/buzz-desktop"
    payload="$macos/buzz-desktop.real"
    infoPlist="$app/Contents/Info.plist"
    runtimeSource="$MESH_RUNTIME_BUNDLE"
    runtimeDestination="$app/Contents/Resources/mesh-runtime"
    entitlements="$PATCHED_BUZZ_SOURCE/desktop/src-tauri/Entitlements.plist"

    if [ ! -d "$sourceApp" ] || [ -L "$sourceApp" ]; then
      echo "Buzz unsigned desktop app is not a regular directory" >&2
      exit 1
    fi
    if [ ! -d "$runtimeSource" ] || [ -L "$runtimeSource" ]; then
      echo "Buzz Mesh runtime source is not a regular directory" >&2
      exit 1
    fi
    if [ ! -f "$runtimeSource/manifest.json" ] || [ -L "$runtimeSource/manifest.json" ]; then
      echo "Buzz Mesh runtime source has no regular manifest" >&2
      exit 1
    fi
    if [ ! -f "$entitlements" ] || [ -L "$entitlements" ]; then
      echo "Buzz patched source has no regular entitlements file" >&2
      exit 1
    fi
    ${entitlementsValidationCommand} "$entitlements" source
    if [ ! -x "$BUZZ_LAUNCHER" ] || [ -L "$BUZZ_LAUNCHER" ]; then
      echo "Buzz launcher is not a regular executable" >&2
      exit 1
    fi

    mkdir -p "$out/Applications"
    cp -R "$sourceApp" "$app"
    find "$app" -type d -exec chmod u+w {} +
    find "$app" -type f -exec chmod u+w {} +
    "$XATTR_TOOL" -cr "$app"

    if [ ! -f "$infoPlist" ] || [ -L "$infoPlist" ]; then
      echo "Buzz unsigned app has no regular Info.plist" >&2
      exit 1
    fi
    if ! "$PLISTBUDDY_TOOL" -c 'Set :LSMinimumSystemVersion 14.0' "$infoPlist"; then
      echo "Buzz candidate could not set its minimum macOS version" >&2
      exit 1
    fi

    if [ ! -x "$launcher" ] || [ -L "$launcher" ]; then
      echo "Buzz unsigned app has no regular main executable" >&2
      exit 1
    fi
    if [ -e "$payload" ] || [ -L "$payload" ]; then
      echo "Buzz payload destination already exists" >&2
      exit 1
    fi
    mv "$launcher" "$payload"
    install -m0755 "$BUZZ_LAUNCHER" "$launcher"

    if ! payloadDependencyListing="$("$OTOOL_TOOL" -L "$payload")"; then
      echo "Buzz candidate could not inspect payload dependencies" >&2
      exit 1
    fi
    iconvDependency=""
    while IFS= read -r dependencyLine; do
      dependency="$(printf '%s\n' "$dependencyLine" | LC_ALL=C awk '{ print $1 }')"
      case "$dependency" in
        /nix/store/*-libiconv-*/lib/libiconv.2.dylib)
          if [ -n "$iconvDependency" ]; then
            echo "Buzz payload has multiple Nix libiconv edges" >&2
            exit 1
          fi
          case "$dependencyLine" in
            *' (compatibility version 7.0.0, '*) ;;
            *)
              echo "Buzz payload libiconv ABI differs from macOS" >&2
              exit 1
              ;;
          esac
          iconvDependency="$dependency"
          ;;
      esac
    done < <(printf '%s\n' "$payloadDependencyListing" | LC_ALL=C awk 'NR > 1')
    if [ -z "$iconvDependency" ]; then
      echo "Buzz payload has no relocatable Nix libiconv edge" >&2
      exit 1
    fi
    "$INSTALL_NAME_TOOL" \
      -change "$iconvDependency" /usr/lib/libiconv.2.dylib "$payload"

    if [ -e "$runtimeDestination" ] || [ -L "$runtimeDestination" ]; then
      echo "Buzz runtime destination already exists" >&2
      exit 1
    fi
    mkdir -p "$runtimeDestination"
    cp -R "$runtimeSource/." "$runtimeDestination/"
    ${runtimeValidationCommand} "$runtimeDestination"

    expectedInventory="$TMPDIR/buzz-candidate-macos.expected"
    actualInventory="$TMPDIR/buzz-candidate-macos.actual"
    unsupportedInventory="$TMPDIR/buzz-candidate-macos.unsupported"
    printf '%s\n' \
      buzz \
      buzz-acp \
      buzz-agent \
      buzz-backend-kubernetes \
      buzz-desktop \
      buzz-desktop.real \
      buzz-dev-mcp \
      git-credential-nostr > "$expectedInventory"
    if ! find "$macos" -mindepth 1 -maxdepth 1 ! -type f \
      -print > "$unsupportedInventory"
    then
      echo "Buzz candidate failed to inspect unsupported MacOS entries" >&2
      exit 1
    fi
    if [ -s "$unsupportedInventory" ]; then
      echo "Buzz candidate contains a non-file MacOS entry" >&2
      exit 1
    fi
    if ! find "$macos" -mindepth 1 -maxdepth 1 -type f \
      -exec basename {} \; | LC_ALL=C sort > "$actualInventory"
    then
      echo "Buzz candidate failed to enumerate MacOS inventory" >&2
      exit 1
    fi
    if ! cmp -s "$expectedInventory" "$actualInventory"; then
      echo "Buzz candidate MacOS inventory is not exact" >&2
      exit 1
    fi

    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      --entitlements "$entitlements" "$payload"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none "$macos/buzz-acp"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none "$macos/buzz-agent"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      "$macos/buzz-backend-kubernetes"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none "$macos/buzz-dev-mcp"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      "$macos/git-credential-nostr"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none "$macos/buzz"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      --entitlements "$entitlements" "$launcher"
    "$CODESIGN_TOOL" --force --sign - --timestamp=none \
      --entitlements "$entitlements" "$app"

    ${runtimeValidationCommand} "$runtimeDestination"
  '';
  launcherSmokeScript = ''
    launcherSmokeRoot="$TMPDIR/buzz-candidate-launcher-smoke"
    launcherSmokeApp="$launcherSmokeRoot/Buzz Smoke.app"
    launcherSmokeMacos="$launcherSmokeApp/Contents/MacOS"
    launcherSmokeRuntime="$launcherSmokeApp/Contents/Resources/mesh-runtime"
    launcherSmokeHome="$launcherSmokeRoot/home"
    launcherSmokeRecord="$launcherSmokeRoot/record"
    launcherSmokeExpected="$launcherSmokeRoot/expected"

    if [ -e "$launcherSmokeRoot" ] || [ -L "$launcherSmokeRoot" ]; then
      echo "Buzz candidate launcher smoke root already exists" >&2
      exit 1
    fi
    mkdir -p "$launcherSmokeMacos" "$launcherSmokeRuntime" "$launcherSmokeHome"
    install -m0755 "$launcher" "$launcherSmokeMacos/buzz-desktop"
    printf '{}\n' > "$launcherSmokeRuntime/manifest.json"
    printf '%s\n' \
      '#!/bin/sh' \
      'set -eu' \
      'if [ "''${MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL+set}" = set ]; then' \
      '  echo "launcher preserved the manifest URL" >&2' \
      '  exit 1' \
      'fi' \
      'printf "%s\n" "$MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR" "$MESH_LLM_NATIVE_RUNTIME_CACHE_DIR" "$1" > "$BUZZ_LAUNCHER_SMOKE_RECORD"' \
      > "$launcherSmokeMacos/buzz-desktop.real"
    chmod 0755 "$launcherSmokeMacos/buzz-desktop.real"

    /usr/bin/env -i \
      HOME="$launcherSmokeHome" \
      BUZZ_LAUNCHER_SMOKE_RECORD="$launcherSmokeRecord" \
      MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR=/hostile/bundle \
      MESH_LLM_NATIVE_RUNTIME_CACHE_DIR=relative-cache \
      MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL=https://example.invalid/runtime.json \
      "$launcherSmokeMacos/buzz-desktop" "probe argument"
    printf '%s\n' \
      "$launcherSmokeRuntime" \
      "$launcherSmokeHome/Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes" \
      "probe argument" \
      > "$launcherSmokeExpected"
    if ! cmp -s "$launcherSmokeExpected" "$launcherSmokeRecord"; then
      echo "Buzz candidate launcher smoke contract differs" >&2
      exit 1
    fi
    if [ -e "$launcherSmokeHome/Library" ] || [ -L "$launcherSmokeHome/Library" ]; then
      echo "Buzz candidate launcher created its runtime cache" >&2
      exit 1
    fi
  '';
  buildPhase = ''
    runHook preBuild
    "$CC" \
      -std=c11 \
      -Wall \
      -Wextra \
      -Werror \
      -Os \
      -mmacosx-version-min=14.0 \
      ${./buzz-launcher.c} \
      -o buzz-launcher
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    export BUZZ_LAUNCHER="$PWD/buzz-launcher"
    export CODESIGN_TOOL=/usr/bin/codesign
    export DESKTOP_UNSIGNED=${desktopUnsigned}
    export INSTALL_NAME_TOOL=${cctools}/bin/install_name_tool
    export MESH_RUNTIME_BUNDLE=${meshRuntimeBundle}
    export OTOOL_TOOL=${cctools}/bin/otool
    export PATCHED_BUZZ_SOURCE=${patchedBuzzSource}
    export PLISTBUDDY_TOOL=/usr/libexec/PlistBuddy
    export PYTHON_TOOL=${python3}/bin/python3
    export XATTR_TOOL=/usr/bin/xattr
    ${assemblyScript}
    runHook postInstall
  '';
  installCheckPhase = ''
    runHook preInstallCheck
    set -o pipefail

    app="$out/Applications/Buzz.app"
    macos="$app/Contents/MacOS"
    launcher="$macos/buzz-desktop"
    payload="$macos/buzz-desktop.real"
    runtime="$app/Contents/Resources/mesh-runtime"
    infoPlist="$app/Contents/Info.plist"
    export PYTHON_TOOL=${python3}/bin/python3

    test -d "$app"
    test -x "$launcher"
    test -x "$payload"
    test ! -e "$app/Contents/embedded.provisionprofile"
    ${runtimeValidationCommand} "$runtime"
    ${launcherSmokeScript}

    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$infoPlist")" = \
      buzz-desktop
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$infoPlist")" = \
      xyz.block.buzz.app
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleName' "$infoPlist")" = \
      Buzz
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$infoPlist")" = \
      ${lib.escapeShellArg buzzVersion}
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$infoPlist")" = \
      ${lib.escapeShellArg buzzVersion}
    test "$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$infoPlist")" = \
      14.0

    expectedInventory="$TMPDIR/buzz-candidate-install-check.expected"
    actualInventory="$TMPDIR/buzz-candidate-install-check.actual"
    unsupportedInventory="$TMPDIR/buzz-candidate-install-check.unsupported"
    printf '%s\n' \
      buzz \
      buzz-acp \
      buzz-agent \
      buzz-backend-kubernetes \
      buzz-desktop \
      buzz-desktop.real \
      buzz-dev-mcp \
      git-credential-nostr > "$expectedInventory"
    if ! find "$macos" -mindepth 1 -maxdepth 1 ! -type f \
      -print > "$unsupportedInventory"
    then
      echo "Buzz candidate failed to inspect unsupported MacOS entries" >&2
      exit 1
    fi
    if [ -s "$unsupportedInventory" ]; then
      echo "Buzz candidate contains a non-file MacOS entry" >&2
      exit 1
    fi
    if ! find "$macos" -mindepth 1 -maxdepth 1 -type f \
      -exec basename {} \; | LC_ALL=C sort > "$actualInventory"
    then
      echo "Buzz candidate failed to enumerate MacOS inventory" >&2
      exit 1
    fi
    cmp -s "$expectedInventory" "$actualInventory"

    for executable in \
      "$launcher" \
      "$payload" \
      "$macos/buzz-acp" \
      "$macos/buzz-agent" \
      "$macos/buzz-backend-kubernetes" \
      "$macos/buzz-dev-mcp" \
      "$macos/git-credential-nostr" \
      "$macos/buzz"
    do
      /usr/bin/file "$executable" | grep -F 'Mach-O 64-bit executable arm64'
      if ! architectures="$(${cctools}/bin/lipo -archs "$executable")"; then
        echo "Buzz candidate lipo failed: $executable" >&2
        exit 1
      fi
      if [ "$architectures" != arm64 ]; then
        echo "Buzz candidate architectures differ from arm64: $executable -> $architectures" >&2
        exit 1
      fi
      ${rpathValidationCommand} "$app" "$executable"
      /usr/bin/codesign --verify --strict "$executable"
      signatureDetails="$(/usr/bin/codesign -dv --verbose=4 "$executable" 2>&1)"
      printf '%s\n' "$signatureDetails" | grep -Fx 'Signature=adhoc'
    done

    runtimeLibraryInventory="$TMPDIR/buzz-candidate-runtime-dylibs"
    if ! find "$runtime/lib" -type f -name '*.dylib' | \
      LC_ALL=C sort > "$runtimeLibraryInventory"
    then
      echo "Buzz candidate failed to enumerate runtime dylibs" >&2
      exit 1
    fi
    if [ ! -s "$runtimeLibraryInventory" ]; then
      echo "Buzz candidate runtime contains no dylibs" >&2
      exit 1
    fi
    while IFS= read -r runtimeLibrary; do
      /usr/bin/codesign --verify --strict "$runtimeLibrary"
    done < "$runtimeLibraryInventory"
    ${runtimeLoadValidationCommand} "$runtime"

    for entitledExecutable in "$launcher" "$payload" "$app"; do
      entitlementDump="$TMPDIR/$(basename "$entitledExecutable").entitlements.plist"
      /usr/bin/codesign -d --entitlements - --xml \
        "$entitledExecutable" > "$entitlementDump" 2>/dev/null
      ${entitlementsValidationCommand} "$entitlementDump" final
    done

    /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"
    runHook postInstallCheck
  '';
in
assert builtins.isString buzzVersion;
assert builtins.isString buzzCommit && builtins.match "[0-9a-f]{40}" buzzCommit != null;
assert builtins.isString rustVersion;
assert builtins.isString pnpmVersion;
assert builtins.isString sherpaVersion;
assert builtins.isString meshLlmVersion;
assert builtins.isString skippyAbi && builtins.match "[0-9]+\\.[0-9]+\\.[0-9]+" skippyAbi != null;
assert version == buzzVersion;
assert (desktopUnsigned.passthru.buzzNativeContract or null) == expectedDesktopContract;
assert (meshRuntimeBundle.passthru.buzzNativeContract or null) == expectedRuntimeContract;
assert (meshRuntimeBundle.passthru.manifestSubpath or null) == "manifest.json";
assert
  (meshRuntimeBundle.passthru.runtimeId or null) == "meshllm-native-runtime-darwin-aarch64-metal";
assert (patchedBuzzSource.passthru.buzzNativeContract or null) == expectedSourceContract;
stdenv.mkDerivation {
  pname = "buzz-desktop-candidate";
  inherit version;
  __structuredAttrs = true;
  strictDeps = true;
  dontUnpack = true;
  dontConfigure = true;
  dontFixup = true;

  nativeBuildInputs = [
    cctools
    python3
  ];

  env.MACOSX_DEPLOYMENT_TARGET = "14.0";
  inherit
    buildPhase
    installCheckPhase
    installPhase
    ;
  doInstallCheck = true;
  outputChecks.out.allowedReferences = [ ];

  passthru = {
    buzzNativeContract = implementedContract;
    macApp = {
      bundleId = "xyz.block.buzz.app";
      bundleName = "Buzz.app";
      bundleRelPath = "Applications/Buzz.app";
      installMode = "copy";
    };
  };

  meta = {
    description = "Source-built Buzz desktop app with an embedded offline Mesh runtime";
    homepage = "https://github.com/block/buzz";
    license = lib.licenses.asl20;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = [ lib.sourceTypes.fromSource ];
  };
}
