{
  autoconf,
  automake,
  callPackage,
  cctools,
  claude-code,
  cmake,
  fetchFromGitHub,
  fetchNpmDeps,
  fetchurl,
  expectedNativeManifest ? null,
  lib,
  libiconv,
  makeWrapper,
  nixcfgElectron,
  nodejs_24,
  npmHooks,
  pkg-config,
  python3,
  selfSource ? builtins.fromJSON (builtins.readFile ./sources.json),
  stdenv,
  stdenvNoCC,
  ...
}:
let
  pname = "paseo";
  appName = "Paseo";
  appBundleName = "${appName}.app";
  appExecutableName = appName;
  appId = "sh.paseo.desktop";

  expectedVersion = "0.6.1";
  expectedCommit = "20d7efc46a316f5a274b9943a5c43b0322269825";
  electronVersion = "41.2.0";
  sherpaVersion = "1.12.28";
  sherpaCommit = "86d3d00e28c22c102fb7d01c7b62fdc4e7a69f1b";
  onnxruntimeVersion = "1.23.2";
  onnxruntimeCommit = "a83fc4d58cb48eb68890dd689f94f28288cf2278";
  expectedOnnxruntimeSourceHash = "sha256-hZ2L5+0Enkw4rGDKVpRECnKXP87w6Kbiyp6Fdxwt6hk=";
  nodeAddonApiVersion = "8.3.0";
  esbuildVersion = "0.25.12";
  claudeAgentSdkVersion = "0.3.220";
  claudeCodeExecutable = lib.getExe claude-code;
  paseoEntitlements = ./Entitlements.plist;
  paseoSignatureValidator = ./validate_signatures.py;
  expectedNodeAddonApiUrl = "https://registry.npmjs.org/node-addon-api/-/node-addon-api-${nodeAddonApiVersion}.tgz";
  expectedSherpaWrapperUrl = "https://registry.npmjs.org/sherpa-onnx-node/-/sherpa-onnx-node-${sherpaVersion}.tgz";

  source = selfSource;
  version = source.version or "unknown";
  urls = source.urls or { };
  paseoUrl = urls.paseo or "";
  sherpaUrl = urls.sherpaOnnx or "";
  onnxruntimeUrl = urls.onnxruntime or "";
  sherpaWrapperUrl = urls.sherpaOnnxNode or "";
  nodeAddonApiUrl = urls.nodeAddonApi or "";

  hashEntryFor =
    hashType: url:
    lib.findFirst (entry: entry.hashType == hashType && (entry.url or null) == url) null (
      source.hashes or [ ]
    );

  paseoSourceHash = hashEntryFor "srcHash" paseoUrl;
  sherpaSourceHash = hashEntryFor "srcHash" sherpaUrl;
  onnxruntimeSourceHash = hashEntryFor "srcHash" onnxruntimeUrl;
  sherpaWrapperHash = hashEntryFor "sha256" sherpaWrapperUrl;
  nodeAddonApiHash = hashEntryFor "sha256" nodeAddonApiUrl;
  npmDepsHash = hashEntryFor "npmDepsHash" paseoUrl;

  electronBuild = nixcfgElectron.sourceBuildFor electronVersion;

  nativeSourceClosureComplete =
    (onnxruntimeExact.passthru.paseoExactSource.sourceClosureComplete or false)
    && (sherpaExact.passthru.paseoExactSource.sourceClosureComplete or false);

  unresolvedBuildGates =
    lib.optional (version != expectedVersion) "Paseo source version must be ${expectedVersion}"
    ++ lib.optional (
      (source.commit or "") != expectedCommit
    ) "Paseo source commit must be ${expectedCommit}"
    ++ lib.optional (
      (source.electronVersion or "") != electronVersion
    ) "Electron must be exactly ${electronVersion}"
    ++ lib.optional (
      electronBuild.runtimeVersion != electronVersion
    ) "Electron runtime and headers must match"
    ++ lib.optional (paseoSourceHash == null) "Paseo srcHash is missing"
    ++ lib.optional (sherpaSourceHash == null) "sherpa-onnx ${sherpaVersion} srcHash is missing"
    ++ lib.optional (
      onnxruntimeSourceHash == null
    ) "ONNX Runtime ${onnxruntimeVersion} srcHash is missing"
    ++ lib.optional (
      onnxruntimeSourceHash != null && onnxruntimeSourceHash.hash != expectedOnnxruntimeSourceHash
    ) "ONNX Runtime source closure hash must be ${expectedOnnxruntimeSourceHash}"
    ++ lib.optional (
      sherpaWrapperUrl != expectedSherpaWrapperUrl
    ) "sherpa-onnx-node wrapper URL must be exactly ${expectedSherpaWrapperUrl}"
    ++ lib.optional (sherpaWrapperHash == null) "sherpa-onnx-node wrapper hash is missing"
    ++ lib.optional (
      nodeAddonApiUrl != expectedNodeAddonApiUrl
    ) "node-addon-api must be exactly ${nodeAddonApiVersion}"
    ++ lib.optional (nodeAddonApiHash == null) "node-addon-api ${nodeAddonApiVersion} hash is missing"
    ++ lib.optional (npmDepsHash == null) "Paseo aarch64-darwin npmDepsHash is unresolved"
    ++ lib.optional (
      expectedNativeManifest == null
    ) "Paseo exact native relative-path/count manifest is unresolved"
    ++ lib.optional (
      !nativeSourceClosureComplete
    ) "Paseo native transitive source closures are incomplete or unvalidated";

  commonPassthru = {
    paseoBuildGates = unresolvedBuildGates;
    exactSourcePlan = {
      electron = electronVersion;
      esbuild = esbuildVersion;
      nodeAddonApi = nodeAddonApiVersion;
      onnxruntime = {
        commit = onnxruntimeCommit;
        version = onnxruntimeVersion;
      };
      sherpa = {
        commit = sherpaCommit;
        version = sherpaVersion;
      };
      sharp = null;
      claudeRuntime = {
        sdkVersion = claudeAgentSdkVersion;
        executable = claudeCodeExecutable;
        platformPackagePruned = true;
        treeSitter = "external-claude-runtime";
      };
      nativeManifest = expectedNativeManifest;
    };
  };

  blockedPackage = stdenvNoCC.mkDerivation {
    inherit pname version;
    dontUnpack = true;
    buildPhase = ''
      echo "Paseo is intentionally unbuildable:" >&2
      ${lib.concatMapStringsSep "\n" (
        gate: "echo ${lib.escapeShellArg "- ${gate}"} >&2"
      ) unresolvedBuildGates}
      exit 1
    '';
    installPhase = "exit 1";
    passthru = commonPassthru // {
      inherit
        onnxruntimeExact
        sherpaExact
        sherpaNodeAddon
        ;
    };
    meta = {
      broken = true;
      description = "Gated exact-source foundation for the Paseo desktop app";
      homepage = "https://github.com/getpaseo/paseo";
      license = lib.licenses.agpl3Plus;
      platforms = [ "aarch64-darwin" ];
    };
  };

  paseoSrc = fetchFromGitHub {
    owner = "getpaseo";
    repo = "paseo";
    rev = expectedCommit;
    inherit (paseoSourceHash) hash;
  };
  sherpaSrc = fetchFromGitHub {
    owner = "k2-fsa";
    repo = "sherpa-onnx";
    rev = sherpaCommit;
    inherit (sherpaSourceHash) hash;
  };
  onnxruntimeSrc = fetchFromGitHub {
    owner = "microsoft";
    repo = "onnxruntime";
    rev = onnxruntimeCommit;
    fetchSubmodules = true;
    inherit (onnxruntimeSourceHash) hash;
  };
  sherpaWrapperSrc = fetchurl {
    url = sherpaWrapperUrl;
    inherit (sherpaWrapperHash) hash;
  };
  nodeAddonApiSrc = fetchurl {
    url = nodeAddonApiUrl;
    inherit (nodeAddonApiHash) hash;
  };
  npmDeps = fetchNpmDeps {
    name = "${pname}-${version}-npm-deps";
    src = paseoSrc;
    inherit (npmDepsHash) hash;
    fetcherVersion = 2;
  };

  onnxruntimeExact = callPackage ./onnxruntime-source.nix {
    src = onnxruntimeSrc;
  };
  sherpaExact = callPackage ./sherpa-source.nix {
    inherit onnxruntimeExact;
    src = sherpaSrc;
  };
  sherpaNodeAddon = callPackage ./sherpa-node-addon.nix {
    inherit
      nodeAddonApiSrc
      onnxruntimeExact
      sherpaExact
      ;
    electronHeaders = electronBuild.headers;
    src = sherpaSrc;
    wrapperSrc = sherpaWrapperSrc;
  };

  realPackage = stdenv.mkDerivation {
    inherit
      npmDeps
      pname
      version
      ;
    src = paseoSrc;

    nativeBuildInputs = [
      autoconf
      automake
      cctools
      cmake
      libiconv
      makeWrapper
      nodejs_24
      npmHooks.npmConfigHook
      pkg-config
      python3
    ];

    strictDeps = true;
    dontUseCmakeConfigure = true;
    dontStrip = true;
    makeCacheWritable = true;
    npmFlags = [ "--legacy-peer-deps" ];
    npmRebuildFlags = [ "--ignore-scripts" ];

    env = electronBuild.commonEnv // {
      CI = "1";
      CLAUDE_CODE_EXECUTABLE = claudeCodeExecutable;
      CSC_IDENTITY_AUTO_DISCOVERY = "false";
      NIX_NPM_FETCHER_VERSION = "2";
      NODE_OPTIONS = "--max-old-space-size=6144";
      npm_config_arch = "arm64";
      npm_config_build_from_source = "true";
    };

    postPatch = ''
      PYTHONPATH=${
        lib.fileset.toSource {
          root = ../..;
          fileset = lib.fileset.unions [
            ../../lib/__init__.py
            ../../lib/exact_text_patch.py
          ];
        }
      } ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD" \
        --claude-code-executable "$CLAUDE_CODE_EXECUTABLE"
    '';

    # Backport electron-builder 2ff9190aadc791503a6e62cdcbfa975448bc49bf
    # until app-builder-lib ships the path-local node-collector cycle guard.
    postConfigure = ''
      appBuilderLibPackage="$PWD/node_modules/app-builder-lib"
      appBuilderLibManifest="$appBuilderLibPackage/package.json"
      appBuilderLibCollector="$appBuilderLibPackage/out/node-module-collector/nodeModulesCollector.js"
      appBuilderLibPatch=${./app-builder-lib-26.8.1-cycle-guard.patch}

      for required in \
        "$appBuilderLibManifest" \
        "$appBuilderLibCollector" \
        "$appBuilderLibPatch"
      do
        if [ ! -f "$required" ]; then
          echo "missing required app-builder-lib cycle-guard input: $required" >&2
          exit 1
        fi
      done

      installedAppBuilderLibVersion="$(${lib.getExe nodejs_24} -e '
        const fs = require("fs");
        const manifest = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
        process.stdout.write(String(manifest.version ?? ""));
      ' "$appBuilderLibManifest")"
      if [ "$installedAppBuilderLibVersion" != 26.8.1 ]; then
        echo \
          "app-builder-lib cycle guard expected 26.8.1, found $installedAppBuilderLibVersion" \
          >&2
        exit 1
      fi

      appBuilderLibPatchOptions=(
        --batch
        --forward
        --fuzz=0
        --strip=1
        --directory="$PWD"
        --input="$appBuilderLibPatch"
      )

      # Validate every hunk before changing the installed dependency tree. This
      # keeps upstream collector drift from leaving partially patched sources.
      patch --dry-run "''${appBuilderLibPatchOptions[@]}"
      patch "''${appBuilderLibPatchOptions[@]}"
      ${lib.getExe nodejs_24} --check "$appBuilderLibCollector"
    '';

    configurePhase = ''
      runHook preConfigure

      rm -rf \
        node_modules/sherpa-onnx-node \
        node_modules/sherpa-onnx-darwin-arm64
      cp -R ${sherpaNodeAddon}/node_modules/. node_modules/
      mkdir -p packages/server/node_modules
      for packageName in sherpa-onnx-node sherpa-onnx-darwin-arm64
      do
        rm -rf "packages/server/node_modules/$packageName"
        ln -s "../../../node_modules/$packageName" "packages/server/node_modules/$packageName"
      done

      # Paseo supplies pathToClaudeCodeExecutable explicitly.  Remove every
      # location where npm may have placed the SDK's opaque optional runtime.
      sdkPlatformPackage="@anthropic-ai/claude-agent-sdk-darwin-arm64"
      for sdkPlatformPath in \
        "node_modules/$sdkPlatformPackage" \
        "packages/server/node_modules/$sdkPlatformPackage" \
        "packages/server/node_modules/@anthropic-ai/claude-agent-sdk/node_modules/$sdkPlatformPackage"
      do
        rm -rf "$sdkPlatformPath"
      done

      patchShebangs node_modules packages/*/node_modules
      runHook postConfigure
    '';

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR/paseo-build-home"
      mkdir -p "$HOME"

      npm run build:app-deps:clean
      (
        cd packages/app
        PASEO_WEB_PLATFORM=electron npx expo export --platform web
      )
      npm run build:server:clean
      npm run build:main --workspace=@getpaseo/desktop
      npm exec -- electron-rebuild \
        -f \
        -v ${electronVersion} \
        --module-dir packages/server \
        --only=node-pty \
        --build-from-source
      rm -rf \
        node_modules/node-pty/prebuilds \
        packages/server/node_modules/node-pty/prebuilds

      nodePtyRelease="packages/server/node_modules/node-pty/build/Release"
      nodePtyAddon="$nodePtyRelease/pty.node"
      nodePtySpawnHelper="$nodePtyRelease/spawn-helper"
      if [ ! -f "$nodePtyAddon" ]; then
        echo "node-pty source rebuild did not produce a regular pty.node: $nodePtyAddon" >&2
        exit 1
      fi
      if [ ! -f "$nodePtySpawnHelper" ] || [ ! -x "$nodePtySpawnHelper" ]; then
        echo "node-pty source rebuild did not produce an executable spawn-helper: $nodePtySpawnHelper" >&2
        exit 1
      fi
      for nodePtyArtifact in "$nodePtyAddon" "$nodePtySpawnHelper"
      do
        nodePtyDescription="$(/usr/bin/file -b "$nodePtyArtifact")"
        case "$nodePtyDescription" in
          *Mach-O*) ;;
          *)
            echo "node-pty source rebuild output is not a Mach-O: $nodePtyArtifact ($nodePtyDescription)" >&2
            exit 1
            ;;
        esac
        nodePtyArchitectures="$(/usr/bin/lipo -archs "$nodePtyArtifact")"
        if [ "$nodePtyArchitectures" != arm64 ]; then
          echo "node-pty source rebuild output is not arm64-only: $nodePtyArtifact ($nodePtyArchitectures)" >&2
          exit 1
        fi
      done

      ${electronBuild.copyDist}

      (
        cd packages/desktop
        ../../node_modules/.bin/electron-builder \
          --config electron-builder.yml \
          --mac \
          --arm64 \
          --dir \
          --publish never \
          -c.mac.identity=null \
          -c.mac.notarize=false \
          -c.npmRebuild=false \
          ${electronBuild.electronBuilderConfigFlags}
      )

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      appBundle="packages/desktop/release/mac-arm64/${appBundleName}"
      if [ ! -d "$appBundle" ]; then
        echo "failed to locate packaged ${appBundleName} at $appBundle" >&2
        exit 1
      fi

      mkdir -p "$out/Applications" "$out/bin" "$out/share/licenses/${pname}"
      cp -R "$appBundle" "$out/Applications/${appBundleName}"
      install -m0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
      makeWrapper \
        "$out/Applications/${appBundleName}/Contents/MacOS/${appExecutableName}" \
        "$out/bin/${pname}"

      runHook postInstall
    '';

    postFixup = ''
      app="$out/Applications/${appBundleName}"
      /usr/bin/xattr -cr "$app"

      machoPaths=()
      while IFS= read -r -d "" candidate
      do
        description="$(/usr/bin/file -b "$candidate")"
        case "$description" in
          *Mach-O*)
            machoPaths+=("$candidate")
            ;;
        esac
      done < <(/usr/bin/find "$app" -type f -print0)

      frameworks=()
      while IFS= read -r -d "" framework
      do
        frameworks+=("$framework")
      done < <(/usr/bin/find "$app/Contents/Frameworks" -type d -name '*.framework' -prune -print0)
      if [ "''${#frameworks[@]}" -ne 4 ]; then
        echo "Paseo signing expected 4 frameworks, found ''${#frameworks[@]}" >&2
        exit 1
      fi
      expectedFrameworks=(
        "$app/Contents/Frameworks/Electron Framework.framework"
        "$app/Contents/Frameworks/Mantle.framework"
        "$app/Contents/Frameworks/ReactiveObjC.framework"
        "$app/Contents/Frameworks/Squirrel.framework"
      )
      for expectedFramework in "''${expectedFrameworks[@]}"
      do
        frameworkFound=false
        for framework in "''${frameworks[@]}"
        do
          if [ "$framework" = "$expectedFramework" ]; then
            frameworkFound=true
            break
          fi
        done
        if [ "$frameworkFound" != true ]; then
          echo "Paseo signing is missing audited framework: $expectedFramework" >&2
          exit 1
        fi
      done

      helpers=()
      while IFS= read -r -d "" helper
      do
        helpers+=("$helper")
      done < <(/usr/bin/find "$app/Contents/Frameworks" -type d -name '*.app' -prune -print0)
      if [ "''${#helpers[@]}" -ne 4 ]; then
        echo "Paseo signing expected 4 helpers, found ''${#helpers[@]}" >&2
        exit 1
      fi
      expectedHelpers=(
        "$app/Contents/Frameworks/Paseo Helper (GPU).app"
        "$app/Contents/Frameworks/Paseo Helper (Plugin).app"
        "$app/Contents/Frameworks/Paseo Helper (Renderer).app"
        "$app/Contents/Frameworks/Paseo Helper.app"
      )
      for expectedHelper in "''${expectedHelpers[@]}"
      do
        helperFound=false
        for helper in "''${helpers[@]}"
        do
          if [ "$helper" = "$expectedHelper" ]; then
            helperFound=true
            break
          fi
        done
        if [ "$helperFound" != true ]; then
          echo "Paseo signing is missing audited helper: $expectedHelper" >&2
          exit 1
        fi
      done

      # Sign every regular Mach-O leaf without entitlements. The four helper
      # bundles and outer app add the reviewed entitlements to only their mains.
      for macho in "''${machoPaths[@]}"
      do
        /usr/bin/codesign \
          --force \
          --timestamp=none \
          --options runtime \
          --sign - \
          "$macho"
      done

      # Re-sign the containing bundles from the deepest level outward.
      for framework in "''${expectedFrameworks[@]}"
      do
        /usr/bin/codesign \
          --force \
          --timestamp=none \
          --options runtime \
          --sign - \
          "$framework"
      done
      for helper in "''${expectedHelpers[@]}"
      do
        /usr/bin/codesign \
          --force \
          --timestamp=none \
          --options runtime \
          --sign - \
          --entitlements ${paseoEntitlements} \
          "$helper"
      done
      /usr/bin/codesign \
        --force \
        --timestamp=none \
        --options runtime \
        --sign - \
        --entitlements ${paseoEntitlements} \
        "$app"
    '';

    doInstallCheck = true;
    installCheckPhase = ''
      runHook preInstallCheck

      app="$out/Applications/${appBundleName}"
      executable="$app/Contents/MacOS/${appExecutableName}"
      resources="$app/Contents/Resources"
      unpacked="$resources/app.asar.unpacked/node_modules"
      packagedEsbuild="$unpacked/@esbuild/darwin-arm64/bin/esbuild"
      packagedNodePtyRelease="$unpacked/node-pty/build/Release"
      packagedNodePtyAddon="$packagedNodePtyRelease/pty.node"
      packagedNodePtySpawnHelper="$packagedNodePtyRelease/spawn-helper"
      plist="$app/Contents/Info.plist"

      for path in \
        "$app" \
        "$executable" \
        "$resources/app.asar" \
        "$packagedEsbuild" \
        "$packagedNodePtyAddon" \
        "$packagedNodePtySpawnHelper" \
        "$unpacked/sherpa-onnx-darwin-arm64/sherpa-onnx.node" \
        "$out/bin/${pname}"
      do
        if [ ! -e "$path" ]; then
          echo "missing required Paseo runtime path: $path" >&2
          exit 1
        fi
      done

      if [ ! -f "$packagedNodePtyAddon" ]; then
        echo "packaged node-pty addon is not a regular file: $packagedNodePtyAddon" >&2
        exit 1
      fi
      if [ ! -f "$packagedNodePtySpawnHelper" ] || [ ! -x "$packagedNodePtySpawnHelper" ]; then
        echo "packaged node-pty spawn-helper is not an executable regular file: $packagedNodePtySpawnHelper" >&2
        exit 1
      fi
      if [ ! -f "$packagedEsbuild" ] || [ ! -x "$packagedEsbuild" ]; then
        echo "packaged esbuild is not an executable regular file: $packagedEsbuild" >&2
        exit 1
      fi
      for packagedNativeArtifact in \
        "$packagedEsbuild" \
        "$packagedNodePtyAddon" \
        "$packagedNodePtySpawnHelper"
      do
        packagedNativeDescription="$(/usr/bin/file -b "$packagedNativeArtifact")"
        case "$packagedNativeDescription" in
          *Mach-O*) ;;
          *)
            echo "packaged native runtime is not a Mach-O: $packagedNativeArtifact ($packagedNativeDescription)" >&2
            exit 1
            ;;
        esac
        packagedNativeArchitectures="$(/usr/bin/lipo -archs "$packagedNativeArtifact")"
        if [ "$packagedNativeArchitectures" != arm64 ]; then
          echo "packaged native runtime is not arm64-only: $packagedNativeArtifact ($packagedNativeArchitectures)" >&2
          exit 1
        fi
      done
      packagedEsbuildVersion="$("$packagedEsbuild" --version)"
      if [ "$packagedEsbuildVersion" != "${esbuildVersion}" ]; then
        echo "packaged esbuild expected ${esbuildVersion}, found $packagedEsbuildVersion" >&2
        exit 1
      fi

      test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")" = "${appId}"
      test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = "${version}"
      test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist")" = "${appExecutableName}"

      asarRoot="$TMPDIR/paseo-asar"
      node_modules/.bin/asar extract "$resources/app.asar" "$asarRoot"
      grep -R -Fq 'Updates are managed by Nix.' "$asarRoot"

      compiledArtifacts="$TMPDIR/paseo-compiled-artifacts"
      claudeExecutableMatches="$TMPDIR/paseo-claude-executable-matches"
      find "$asarRoot" -type f \( \
        -name '*.js' -o -name '*.cjs' -o -name '*.mjs' \
      \) -print0 > "$compiledArtifacts"
      : > "$claudeExecutableMatches"
      while IFS= read -r -d "" compiledArtifact
      do
        if grep -H -F -o -- \
          "$CLAUDE_CODE_EXECUTABLE" \
          "$compiledArtifact" >> "$claudeExecutableMatches"
        then
          :
        else
          grepStatus=$?
          if [ "$grepStatus" -ne 1 ]; then
            exit "$grepStatus"
          fi
        fi
      done < "$compiledArtifacts"
      claudeExecutableMatchCount="$(wc -l < "$claudeExecutableMatches")"
      if [ "$claudeExecutableMatchCount" -ne 4 ]; then
        echo "expected exactly four compiled Paseo Claude executable matches, found $claudeExecutableMatchCount" >&2
        cat "$claudeExecutableMatches" >&2
        exit 1
      fi
      if find "$asarRoot" "$resources/app.asar.unpacked" \
        -path '*/@anthropic-ai/claude-agent-sdk-darwin-arm64*' -print -quit \
        | grep -q .
      then
        echo "opaque Claude Agent SDK platform runtime entered the Paseo bundle" >&2
        exit 1
      fi
      if find "$asarRoot" "$resources/app.asar.unpacked" \
        -path '*/node_modules/sharp/*' -print -quit | grep -q .
      then
        echo "Sharp unexpectedly entered the Paseo desktop production graph" >&2
        exit 1
      fi
      PASEO_PYTHON=${lib.getExe python3} \
        bash ${./validate-native-bundle.sh} \
        "$app" \
        "$asarRoot" \
        ${expectedNativeManifest} \
        ${lib.escapeShellArg appExecutableName}

      ${lib.getExe python3} ${paseoSignatureValidator} "$app"

      # Run the native addons inside the exact packaged Electron runtime without
      # starting the GUI. This checks the Electron 41.2.0 ABI, not just lipo output.
      env \
        -u DYLD_LIBRARY_PATH \
        -u DYLD_FRAMEWORK_PATH \
        -u DYLD_INSERT_LIBRARIES \
        PASEO_EXPECTED_ELECTRON="${electronVersion}" \
        PASEO_RESOURCES="$resources" \
        PASEO_NODE_PTY_RELEASE="$packagedNodePtyRelease" \
        ELECTRON_RUN_AS_NODE=1 \
        "$executable" <<'NODE'
      const fs = require("node:fs");
      const path = require("node:path");

      if (process.versions.electron !== process.env.PASEO_EXPECTED_ELECTRON) {
        throw new Error(
          `expected Electron ''${process.env.PASEO_EXPECTED_ELECTRON}, got ''${process.versions.electron}`,
        );
      }
      const nodeModules = path.join(process.env.PASEO_RESOURCES, "app.asar", "node_modules");
      const nodePtyAddon = path.join(process.env.PASEO_NODE_PTY_RELEASE, "pty.node");
      const nodePtySpawnHelper = path.join(
        process.env.PASEO_NODE_PTY_RELEASE,
        "spawn-helper",
      );
      for (const nativeArtifact of [nodePtyAddon, nodePtySpawnHelper]) {
        if (!fs.statSync(nativeArtifact).isFile()) {
          throw new Error("packaged node-pty runtime is not a regular file: " + nativeArtifact);
        }
      }
      fs.accessSync(nodePtySpawnHelper, fs.constants.X_OK);
      require(nodePtyAddon);
      const nodePty = require(path.join(nodeModules, "node-pty"));
      if (typeof nodePty.spawn !== "function") {
        throw new Error("packaged node-pty did not expose spawn()");
      }
      require(path.join(nodeModules, "sherpa-onnx-node"));
      NODE

      runHook postInstallCheck
    '';

    passthru = commonPassthru // {
      inherit
        electronBuild
        npmDeps
        onnxruntimeExact
        sherpaExact
        sherpaNodeAddon
        ;
      macApp = {
        bundleId = appId;
        bundleName = appBundleName;
        bundleRelPath = "Applications/${appBundleName}";
        installMode = "copy";
      };
    };

    meta = {
      description = "Voice-controlled environment for local AI coding agents";
      homepage = "https://github.com/getpaseo/paseo";
      license = lib.licenses.agpl3Plus;
      mainProgram = pname;
      platforms = [ "aarch64-darwin" ];
    };
  };
in
if unresolvedBuildGates == [ ] then realPackage else blockedPackage
