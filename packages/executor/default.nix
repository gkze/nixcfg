{
  bun,
  bun2nix,
  cctools,
  fetchFromGitHub,
  fetchurl,
  gnupatch,
  inputs,
  lib,
  libarchive,
  makeWrapper,
  nixcfgElectron,
  nodejs_24,
  outputs,
  pkgs,
  python3,
  runCommand,
  selfSource,
  stdenv,
  stdenvNoCC,
  zig_0_15,
  ...
}:
let
  pname = "executor";
  appName = "Executor";
  appBundleName = "${appName}.app";
  appExecutableName = appName;
  appId = "sh.executor.desktop";
  inherit (selfSource) version;
  inherit (selfSource) electronVersion;
  executorEntitlements = ./entitlements.plist;
  executorEntitlementKeys = [
    "com.apple.security.cs.allow-dyld-environment-variables"
    "com.apple.security.cs.allow-jit"
    "com.apple.security.cs.allow-unsigned-executable-memory"
    "com.apple.security.cs.disable-library-validation"
  ];
  executorRequiredResourcePaths = [
    "executor"
    "emscripten-module.wasm"
    "keyring.node"
    "libsql.node"
    "mcp-app.html"
    "onepassword-core_bg.wasm"
    "workerd"
    "worker-bundler/dist/esbuild.wasm"
    "worker-bundler/dist/index.bundled.js"
    "worker-bundler/dist/index.js"
  ];
  executorNativeResourcePaths = [
    "executor"
    "keyring.node"
    "libsql.node"
    "workerd"
  ];
  executorNativeMinimumMacosVersions = [
    {
      path = "executor";
      version = "13.0";
    }
    {
      path = "keyring.node";
      version = "11.0";
    }
    {
      path = "libsql.node";
      version = "14.0";
    }
    {
      path = "workerd";
      version = "13.5";
    }
  ];
  minimumMacosVersion = "14.0";
  executorWasmResourcePaths = [
    "emscripten-module.wasm"
    "onepassword-core_bg.wasm"
    "worker-bundler/dist/esbuild.wasm"
  ];
  executorWasmResourceArguments = lib.concatMapStringsSep " " (
    path: ''"$executorResources/${path}"''
  ) executorWasmResourcePaths;
  executorManagedPolicyProbes = [
    {
      message = "Nix-managed Executor cannot install a mutable background service.";
      arguments = [
        "install"
        "--port"
        "49213"
      ];
    }
    {
      message = "Nix-managed Executor cannot install a mutable background service.";
      arguments = [
        "service"
        "install"
        "--port"
        "49213"
      ];
    }
    {
      message = "Nix-managed Executor cannot uninstall a mutable background service.";
      arguments = [
        "service"
        "uninstall"
      ];
    }
    {
      message = "Nix-managed Executor cannot restart a mutable background service.";
      arguments = [
        "service"
        "restart"
      ];
    }
  ];

  bunSourceMetadata = outputs.lib.sourceHashEntry pname "sha256";
  bunVersionMatch = builtins.match ".*/bun-v([^/]+)/.*" bunSourceMetadata.url;
  bunVersion =
    if bunVersionMatch == null then
      throw "Executor updater produced an invalid Bun source URL: ${bunSourceMetadata.url}"
    else
      builtins.head bunVersionMatch;
  bunSource = fetchurl {
    inherit (bunSourceMetadata) hash url;
  };
  bunExact = bun.overrideAttrs (previousAttrs: {
    version = bunVersion;
    src = bunSource;
    passthru = (previousAttrs.passthru or { }) // {
      sources = {
        aarch64-darwin = bunSource;
      };
    };
  });

  src = fetchFromGitHub {
    owner = "UsefulSoftwareCo";
    repo = "executor";
    rev = selfSource.commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  electronBuild = nixcfgElectron.sourceBuildFor electronVersion;
  electronRuntime = electronBuild.runtime;
  electronRuntimeVersion = electronBuild.runtimeVersion;
  electronHeaders = electronBuild.headers;
  electronDist = electronBuild.dist;
  electronRuntimeVersionCheck =
    if electronRuntimeVersion == electronVersion then
      true
    else
      throw ''
        packages/executor/default.nix needs Electron ${electronVersion},
        but the selected runtime is ${electronRuntimeVersion}; add the exact runtime to nixcfgElectron
      '';

  invalidBunNixErr = ''
    packages/executor/bun.nix failed to evaluate.

    Regenerate it through packages/executor/updater.py from the pinned bun.lock.
  '';
  copyBunWorkspacePathToStore =
    path:
    let
      generatedRoot = "${toString ./.}/";
      pathString = toString path;
      relativePath = lib.removePrefix generatedRoot pathString;
      derivationName = lib.replaceStrings [ "/" ] [ "-" ] relativePath;
    in
    assert lib.assertMsg (lib.hasPrefix generatedRoot pathString)
      "Executor bun.nix referenced a path outside its generated workspace: ${pathString}";
    runCommand "executor-bun-workspace-${derivationName}" { inherit src; } ''
      mkdir -p "$out"
      cp -R "$src/${relativePath}/." "$out"
    '';

  bunWithFakeNode = stdenvNoCC.mkDerivation {
    name = "executor-bun-with-fake-node";
    nativeBuildInputs = [ makeWrapper ];
    dontUnpack = true;
    dontBuild = true;

    installPhase = ''
      runHook preInstall

      cp -R "${bunExact}/." "$out"
      chmod u+w "$out/bin"
      for nodeBinary in node npm bunx; do
        if [ ! -e "$out/bin/$nodeBinary" ]; then
          ln -s "$out/bin/bun" "$out/bin/$nodeBinary"
        fi
      done
      makeWrapper "$out/bin/bunx" "$out/bin/npx"

      runHook postInstall
    '';
  };
  bunCacheEntryCreator = stdenvNoCC.mkDerivation {
    pname = "executor-bun2nix-cache-entry-creator";
    inherit (bun2nix) version;
    src = inputs.bun2nix + "/programs/cache-entry-creator";

    nativeBuildInputs = [ zig_0_15.hook ];
    postConfigure = ''
      ln -s ${pkgs.callPackage (inputs.bun2nix + "/programs/cache-entry-creator/deps.nix") { }} \
        "$ZIG_GLOBAL_CACHE_DIR/p"
    '';
    zigBuildFlags = [ "--release=fast" ];
    doCheck = true;

    meta.mainProgram = "cache_entry_creator";
  };

  bunPackages = lib.filterAttrs (_: value: lib.isStorePath value) (
    builtins.addErrorContext invalidBunNixErr (
      pkgs.callPackage ./bun.nix {
        copyPathToStore = copyBunWorkspacePathToStore;
      }
    )
  );
  bunPackageEntries = lib.mapAttrsToList (name: package: { inherit name package; }) bunPackages;
  bunPackageShards = lib.groupBy (
    entry: builtins.substring 0 2 (builtins.hashString "sha256" entry.name)
  ) bunPackageEntries;
  resolvePinnedPatch =
    _: patch:
    if lib.hasPrefix "source:" patch then
      src + "/${lib.removePrefix "source:" patch}"
    else if lib.hasPrefix "local:" patch then
      ./. + "/${lib.removePrefix "local:" patch}"
    else
      throw "Executor updater emitted an unsupported patch source";
  patchMetadataPinNames = [
    "bunLockPatch"
    "effectLspPatchVersion"
  ];
  bunLockPatch = resolvePinnedPatch "bunLockPatch" selfSource.pins.bunLockPatch;
  effectLspPatchVersion = selfSource.pins.effectLspPatchVersion;
  patchedBunDependencies = lib.mapAttrs resolvePinnedPatch (
    builtins.removeAttrs selfSource.pins patchMetadataPinNames
  );
  extractHost =
    url:
    let
      match = builtins.match "https?://([^/]+).*" url;
    in
    if match != null then builtins.elemAt match 0 else null;
  registryHostFor =
    package:
    let
      packageUrl = package.passthru.url or null;
      host = if packageUrl != null then extractHost packageUrl else null;
    in
    if host != null && host != "registry.npmjs.org" then host else null;
  buildBunShard =
    shard: entries:
    stdenv.mkDerivation {
      name = "executor-bun-cache-shard-${shard}";
      nativeBuildInputs = [
        bunWithFakeNode
        gnupatch
      ];
      phases = [
        "extractPhase"
        "patchPhase"
        "cacheEntryPhase"
      ];

      extractPhase = ''
        runHook preExtract

        ${lib.concatMapStringsSep "\n" (
          { name, package }:
          ''
            destination="$out/share/bun-packages/${name}"
            mkdir -p "$destination"
            if [ -d "${package}" ]; then
              cp -R "${package}/." "$destination"
            else
              ${lib.getExe' libarchive "bsdtar"} \
                --extract \
                --file "${package}" \
                --strip-components=1 \
                --no-same-owner \
                --no-same-permissions \
                --directory "$destination"
            fi
            chmod -R u+rwX "$destination"
          ''
        ) entries}

        runHook postExtract
      '';

      patchPhase = ''
        runHook prePatch

        ${lib.concatMapStringsSep "\n" (
          { name, ... }:
          let
            packagePatch = patchedBunDependencies.${name} or null;
          in
          lib.optionalString (packagePatch != null) ''
            (cd "$out/share/bun-packages/${name}" && patch -p1 < "${packagePatch}")
          ''
        ) entries}
        patchShebangs "$out/share/bun-packages"

        runHook postPatch
      '';

      cacheEntryPhase = ''
        runHook preCacheEntry

        ${lib.concatMapStringsSep "\n" (
          { name, package }:
          let
            registryHost = registryHostFor package;
          in
          ''
            "${lib.getExe bunCacheEntryCreator}" \
              --out "$out/share/bun-cache" \
              --name "${name}" \
              --package "$out/share/bun-packages/${name}" \
              ${lib.optionalString (registryHost != null) ''
                --registry "${registryHost}"
              ''}
          ''
        ) entries}

        runHook postCacheEntry
      '';

      preferLocalBuild = true;
    };
  shardSizes = builtins.map builtins.length (builtins.attrValues bunPackageShards);
  bunDeps = pkgs.symlinkJoin {
    name = "executor-bun-cache";
    paths = builtins.attrValues (builtins.mapAttrs buildBunShard bunPackageShards);
    passthru.nixcfg = {
      packageCount = builtins.length bunPackageEntries;
      shardCount = builtins.length shardSizes;
      maxShardSize = builtins.foldl' lib.max 0 shardSizes;
      minShardSize = builtins.foldl' lib.min (builtins.head shardSizes) (builtins.tail shardSizes);
    };
  };

  srcWithBun = stdenvNoCC.mkDerivation {
    pname = "${pname}-src-with-bun";
    inherit version src;
    dontUnpack = true;
    dontFixup = true;

    installPhase = ''
      runHook preInstall

      mkdir -p "$out"
      cp -R "$src"/. "$out"
      chmod -R u+w "$out"
      cp ${./bun.lock} "$out/bun.lock"

      runHook postInstall
    '';
  };

  package = stdenv.mkDerivation {
    inherit pname version;
    src = srcWithBun;

    nativeBuildInputs = [
      bunExact
      bun2nix.hook
      cctools
      gnupatch
      libarchive
      makeWrapper
      nodejs_24
      python3
    ];

    strictDeps = true;
    __darwinAllowLocalNetworking = true;
    dontRunLifecycleScripts = true;
    dontStrip = true;
    inherit bunDeps;
    bunInstallFlags = [
      "--linker=isolated"
      "--backend=symlink"
      "--frozen-lockfile"
    ];

    env = electronBuild.commonEnv // {
      CI = "1";
      CSC_IDENTITY_AUTO_DISCOVERY = "false";
      EXECUTOR_DISABLE_UPDATE_CHECK = "1";
      NODE_OPTIONS = "--max-old-space-size=6144";
    };

    postBunSetInstallCacheDirPhase = ''
      ${stdenv.shell} ${./materialize-mutable-bun-cache.sh} \
        "$BUN_INSTALL_CACHE_DIR" \
        "$bunDeps/share/bun-cache" \
        /nix/store \
        ${lib.getExe python3}
    '';

    postPatch = ''
      patch -p1 < ${bunLockPatch}
      PYTHONPATH=${
        lib.fileset.toSource {
          root = ../..;
          fileset = lib.fileset.unions [
            ../../lib/__init__.py
            ../../lib/exact_text_patch.py
          ];
        }
      } ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD"
    '';

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR/executor-home"
      export BUN_TARGET=bun-darwin-arm64
      export EXECUTOR_VERSION=${lib.escapeShellArg version}
      mkdir -p "$HOME"

      test "$(bun --version)" = "${bunVersion}"
      bun run prepare
      grep -Fq '"use effect-lsp-patch-version ${effectLspPatchVersion}";' \
        node_modules/typescript/lib/typescript.js
      grep -Fq '"use effect-lsp-patch-version ${effectLspPatchVersion}";' \
        node_modules/typescript/lib/_tsc.js
      bun run --filter @executor-js/local build

      (
        cd apps/desktop
        bun ./scripts/build-sidecar.ts
        bun run test:smoke
        bunx --bun electron-vite build

        ${electronBuild.copyDist}

        bunx --bun electron-builder \
          --mac \
          --arm64 \
          --dir \
          --publish never \
          --config electron-builder.config.ts \
          -c.mac.identity=null \
          -c.mac.hardenedRuntime=false \
          -c.mac.notarize=false \
          -c.mac.minimumSystemVersion=${minimumMacosVersion} \
          -c.npmRebuild=false \
          ${electronBuild.electronBuilderConfigFlags}
      )

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      appBundle="apps/desktop/dist/mac-arm64/${appBundleName}"
      if [ ! -d "$appBundle" ]; then
        echo "failed to locate packaged ${appBundleName} at $appBundle" >&2
        exit 1
      fi

      mkdir -p "$out/Applications" "$out/bin" "$out/share/licenses/${pname}"
      cp -R "$appBundle" "$out/Applications/${appBundleName}"
      /usr/bin/plutil -replace LSMinimumSystemVersion -string \
        "${minimumMacosVersion}" \
        "$out/Applications/${appBundleName}/Contents/Info.plist"
      install -m0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
      ln -s \
        "$out/Applications/${appBundleName}/Contents/Resources/executor/executor" \
        "$out/bin/${pname}"

      runHook postInstall
    '';

    postFixup = ''
      app="$out/Applications/${appBundleName}"
      cli="$app/Contents/Resources/executor/executor"
      entitlements="${executorEntitlements}"
      /usr/bin/xattr -cr "$app"
      /usr/bin/codesign --force --sign - --options runtime \
        --entitlements "$entitlements" "$cli"
      /usr/bin/codesign --force --deep --sign - --options runtime \
        --entitlements "$entitlements" "$app"
    '';

    doInstallCheck = true;
    installCheckPhase = ''
      runHook preInstallCheck

      app="$out/Applications/${appBundleName}"
      executable="$app/Contents/MacOS/${appExecutableName}"
      resources="$app/Contents/Resources"
      executorResources="$resources/executor"
      cli="$executorResources/executor"
      plist="$app/Contents/Info.plist"

      for path in \
        "$app" \
        "$executable" \
        "$resources/app.asar" \
        "$cli" \
        "$out/bin/${pname}"
      do
        if [ ! -e "$path" ]; then
          echo "missing required Executor runtime path: $path" >&2
          exit 1
        fi
      done

      for relativePath in ${
        lib.concatMapStringsSep " " lib.escapeShellArg executorRequiredResourcePaths
      }; do
        path="$executorResources/$relativePath"
        if [ ! -s "$path" ]; then
          echo "missing or empty required Executor sidecar resource: $relativePath" >&2
          exit 1
        fi
      done
      for target in "$cli" "$executorResources/workerd"; do
        if [ ! -x "$target" ]; then
          echo "required Executor sidecar is not executable: $target" >&2
          exit 1
        fi
      done

      test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")" = "${appId}"
      test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = "${version}"
      test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$plist")" = "${version}"
      test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist")" = \
        "${appExecutableName}"
      test "$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$plist")" = \
        "${minimumMacosVersion}"
      test "$(realpath "$out/bin/${pname}")" = "$cli"
      test "$(ELECTRON_RUN_AS_NODE=1 "$executable" -p 'process.versions.electron')" = \
        "${electronVersion}"
      test "$("$out/bin/${pname}" --version)" = "executor v${version}"
      "$executorResources/workerd" --version >/dev/null
      grep -Fq 'name="executor-mcp-apps-shell"' "$executorResources/mcp-app.html"
      ${lib.getExe bunExact} -e '
        for (const path of process.argv.slice(1)) {
          new WebAssembly.Module(await Bun.file(path).arrayBuffer());
        }
      ' ${executorWasmResourceArguments}

      /usr/bin/lipo "$executable" -verify_arch arm64
      for relativePath in ${
        lib.concatMapStringsSep " " lib.escapeShellArg executorNativeResourcePaths
      }; do
        target="$executorResources/$relativePath"
        /usr/bin/lipo "$target" -verify_arch arm64
        /usr/bin/codesign --verify --strict "$target"
      done
      for specification in ${
        lib.concatMapStringsSep " " (
          resource: lib.escapeShellArg "${resource.path}=${resource.version}"
        ) executorNativeMinimumMacosVersions
      }; do
        relativePath="''${specification%%=*}"
        expectedMinimum="''${specification#*=}"
        loadCommandsFile="$TMPDIR/executor-$relativePath-load-commands"
        /usr/bin/otool -l "$executorResources/$relativePath" > "$loadCommandsFile"
        actualMinimum="$(
          awk '
            $1 == "cmd" && $2 == "LC_BUILD_VERSION" { inBuildVersion = 1; next }
            inBuildVersion && $1 == "minos" { print $2; inBuildVersion = 0 }
          ' "$loadCommandsFile"
        )"
        test "$actualMinimum" = "$expectedMinimum"
      done
      /usr/bin/codesign --verify --deep --strict "$app"
      entitlementsPlist="$TMPDIR/executor-entitlements.plist"
      for target in "$executable" "$cli"; do
        /usr/bin/codesign --verify --strict "$target"
        signatureDetails="$(
          /usr/bin/codesign --display --verbose=4 "$target" 2>&1
        )"
        grep -Fq 'flags=0x10002(adhoc,runtime)' <<< "$signatureDetails"
        /usr/bin/codesign --display --entitlements - --xml "$target" \
          2>/dev/null > "$entitlementsPlist"
        for entitlement in ${lib.concatMapStringsSep " " lib.escapeShellArg executorEntitlementKeys}; do
          value="$(
            /usr/libexec/PlistBuddy -c "Print :$entitlement" "$entitlementsPlist"
          )"
          if [ "$value" != true ]; then
            echo "missing required Executor entitlement $entitlement on $target" >&2
            exit 1
          fi
        done
      done

      probeRoot="$TMPDIR/executor-managed-policy"
      probeHome="$probeRoot/home"
      fakeBin="$probeRoot/fake-bin"
      launchctlLog="$probeRoot/launchctl.log"
      launchAgents="$probeHome/Library/LaunchAgents"
      mkdir -p \
        "$fakeBin" \
        "$launchAgents" \
        "$probeRoot/tmp" \
        "$probeHome/data" \
        "$probeHome/config" \
        "$probeHome/cache" \
        "$probeHome/state" \
        "$probeHome/scope"
      printf '%s\n' sentinel > "$launchAgents/sh.executor.daemon.plist"
      : > "$launchctlLog"
      {
        printf '%s\n' '#!/bin/sh'
        printf '%s\n' 'printf "%s\n" "$*" >> "$EXECUTOR_PROBE_LAUNCHCTL_LOG"'
        printf '%s\n' 'exit 97'
      } > "$fakeBin/launchctl"
      chmod 0755 "$fakeBin/launchctl"

      snapshot_launchagents() {
        (
          cd "$launchAgents"
          find . -print | LC_ALL=C sort
          find . -type f -exec /usr/bin/shasum -a 256 {} \; | LC_ALL=C sort
        ) > "$1"
      }
      probe_failure() {
        cli="$1"
        expected="$2"
        shift 2
        : > "$launchctlLog"
        snapshot_launchagents "$probeRoot/before"
        if env -i \
          HOME="$probeHome" \
          USER=executor-probe \
          LOGNAME=executor-probe \
          PATH="$fakeBin:/usr/bin:/bin" \
          TMPDIR="$probeRoot/tmp" \
          XDG_DATA_HOME="$probeHome/data" \
          XDG_CONFIG_HOME="$probeHome/config" \
          XDG_CACHE_HOME="$probeHome/cache" \
          XDG_STATE_HOME="$probeHome/state" \
          EXECUTOR_DATA_DIR="$probeHome/data/executor" \
          EXECUTOR_SCOPE_DIR="$probeHome/scope" \
          EXECUTOR_DISABLE_UPDATE_CHECK=1 \
          EXECUTOR_DISABLE_ANALYTICS=1 \
          EXECUTOR_DISABLE_INTEGRATIONS_FETCH=1 \
          DO_NOT_TRACK=1 \
          NO_COLOR=1 \
          CI=1 \
          EXECUTOR_PROBE_LAUNCHCTL_LOG="$launchctlLog" \
          "$cli" "$@" > "$probeRoot/output" 2>&1
        then
          echo "Executor managed-policy probe unexpectedly succeeded: $*" >&2
          exit 1
        fi
        snapshot_launchagents "$probeRoot/after"
        grep -Fq "$expected" "$probeRoot/output"
        cmp "$probeRoot/before" "$probeRoot/after"
        test ! -s "$launchctlLog"
      }
      for cli in "$cli" "$out/bin/${pname}"; do
        ${lib.concatMapStringsSep "\n" (probe: ''
          probe_failure "$cli" ${lib.escapeShellArg probe.message} ${
            lib.concatMapStringsSep " " lib.escapeShellArg probe.arguments
          }
        '') executorManagedPolicyProbes}
      done

      grep -a -Fq 'Updates are managed by Nix.' "$resources/app.asar"
      grep -a -Fq 'Nix-managed Executor cannot install a mutable background service.' \
        "$resources/app.asar"
      grep -a -Fq \
        'Nix-managed Executor cannot rotate a supervised daemon token from the app.' \
        "$resources/app.asar"
      grep -a -Fq 'Nix-managed Executor cannot reset data from the app.' \
        "$resources/app.asar"

      if find "$app" -path '*/Library/LaunchAgents/*' -print -quit | grep -q .; then
        echo "unexpected app-bundled LaunchAgent payload" >&2
        exit 1
      fi

      runHook postInstallCheck
    '';

    passthru = {
      inherit
        bunExact
        bun2nix
        bunDeps
        electronDist
        electronHeaders
        electronRuntime
        electronRuntimeVersion
        electronVersion
        ;

      # macApps routing intentionally keeps the app-bearing derivation out of
      # home.packages. This app-free view exposes the colocated CLI while keeping
      # its sibling runtime resources in the main package closure.
      cliPackage = runCommand "${pname}-cli-${version}" { } ''
        mkdir -p "$out/bin"
        ln -s \
          "${package}/Applications/${appBundleName}/Contents/Resources/executor/executor" \
          "$out/bin/${pname}"
      '';

      macApp = {
        bundleId = appId;
        bundleName = appBundleName;
        bundleRelPath = "Applications/Executor.app";
        installMode = "copy";
      };
    };

    meta = {
      description = "Local AI executor with a desktop app, CLI, API server, and web UI";
      homepage = "https://github.com/UsefulSoftwareCo/executor";
      license = lib.licenses.mit;
      mainProgram = pname;
      platforms = [ "aarch64-darwin" ];
      sourceProvenance = [
        lib.sourceTypes.fromSource
        lib.sourceTypes.binaryNativeCode
      ];
    };
  };
in
assert stdenv.hostPlatform.system == "aarch64-darwin";
assert electronRuntimeVersionCheck;
package
