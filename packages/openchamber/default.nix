{
  autoconf,
  automake,
  cacert,
  callPackage,
  cctools,
  cmake,
  fetchFromGitHub,
  fetchurl,
  lib,
  libiconv,
  makeWrapper,
  models-dev,
  nixcfgElectron,
  nodejs_24,
  onnxruntime,
  pkg-config,
  python3,
  ripgrep,
  selfSource,
  sherpa-onnx,
  stdenv,
  stdenvNoCC,
  sysctl,
  ...
}:
let
  pname = "openchamber";
  appName = "OpenChamber";
  appBundleName = "${appName}.app";
  appExecutableName = appName;
  appId = "dev.openchamber.desktop";
  inherit (selfSource) electronVersion version;

  bunVersion = selfSource.pins.bunVersion;
  openCodeCommit = selfSource.pins.opencodeCommit;
  openCodeVersion = selfSource.pins.opencodeVersion;
  sherpaCommit = selfSource.pins.sherpaCommit;
  sherpaVersion = selfSource.pins.sherpaVersion;
  sherpaWrapperVersion = selfSource.pins.sherpaWrapperVersion;
  expectedBunUrl = "https://github.com/oven-sh/bun/releases/download/bun-v${bunVersion}/bun-darwin-aarch64.zip";
  electronBuilderExecutable = "./node_modules/.bin/electron-builder";
  asarExecutable = "node_modules/.bun/node_modules/@electron/asar/bin/asar.js";
  electronExcludedRuntimePackages = [ "bun-pty" ];
  nodePtyDiscardedRuntimeSubtrees = [
    "bin"
    "prebuilds"
  ];
  forbiddenRuntimeBinaryFormats = [
    "*ELF*"
    "*PE32*"
  ];

  urls = selfSource.urls or { };
  bunUrl = urls.bun or "";
  openChamberUrl = urls.openchamber or "";
  openCodeUrl = urls.opencode or "";
  sherpaUrl = urls.sherpaOnnx or "";
  sherpaWrapperUrl = urls.sherpaOnnxNode or "";
  nodeAddonApiUrl = urls.nodeAddonApi or "";

  hashEntryFor =
    hashType: url: platform:
    lib.findFirst (
      entry:
      entry.hashType == hashType
      && (entry.url or null) == url
      && (platform == null || (entry.platform or null) == platform)
    ) null selfSource.hashes;

  openChamberSourceHash = hashEntryFor "srcHash" openChamberUrl null;
  openCodeSourceHash = hashEntryFor "srcHash" openCodeUrl null;
  sherpaSourceHash = hashEntryFor "srcHash" sherpaUrl null;
  bunHash = hashEntryFor "sha256" bunUrl null;
  openCodeNodeModulesHash = hashEntryFor "nodeModulesHash" openCodeUrl "aarch64-darwin";
  openChamberNodeModulesHash = hashEntryFor "nodeModulesHash" openChamberUrl "aarch64-darwin";
  sherpaWrapperHash = hashEntryFor "sha256" sherpaWrapperUrl null;
  nodeAddonApiHash = hashEntryFor "sha256" nodeAddonApiUrl null;

  electronBuild = nixcfgElectron.sourceBuildFor electronVersion;

  expectedNativeRelativePaths = [
    "Contents/MacOS/OpenChamber"
    "Contents/Frameworks/OpenChamber Helper.app/Contents/MacOS/OpenChamber Helper"
    "Contents/Frameworks/OpenChamber Helper (GPU).app/Contents/MacOS/OpenChamber Helper (GPU)"
    "Contents/Frameworks/OpenChamber Helper (Plugin).app/Contents/MacOS/OpenChamber Helper (Plugin)"
    "Contents/Frameworks/OpenChamber Helper (Renderer).app/Contents/MacOS/OpenChamber Helper (Renderer)"
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/chrome_crashpad_handler"
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libEGL.dylib"
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libGLESv2.dylib"
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libffmpeg.dylib"
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libvk_swiftshader.dylib"
    "Contents/Frameworks/Mantle.framework/Versions/A/Mantle"
    "Contents/Frameworks/ReactiveObjC.framework/Versions/A/ReactiveObjC"
    "Contents/Frameworks/Squirrel.framework/Versions/A/Squirrel"
    "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt"
    "Contents/Resources/opencode-cli/opencode"
    "Contents/Resources/app.asar.unpacked/node_modules/node-pty/build/Release/pty.node"
    "Contents/Resources/app.asar.unpacked/node_modules/node-pty/build/Release/spawn-helper"
    "Contents/Resources/app.asar.unpacked/node_modules/sherpa-onnx-darwin-arm64/sherpa-onnx.node"
    "Contents/Resources/app.asar.unpacked/node_modules/sherpa-onnx-darwin-arm64/libsherpa-onnx-c-api.dylib"
    "Contents/Resources/app.asar.unpacked/node_modules/sherpa-onnx-darwin-arm64/libonnxruntime.1.dylib"
  ];

  unresolvedBuildGates =
    lib.optional (
      electronBuild.runtimeVersion != electronVersion
    ) "Electron runtime and headers must match"
    ++ lib.optional (bunUrl != expectedBunUrl) "Bun asset URL must be ${expectedBunUrl}"
    ++ lib.optional (
      sherpa-onnx.version != sherpaVersion
    ) "nixpkgs sherpa-onnx must be exactly ${sherpaVersion}"
    ++ lib.optional (openChamberSourceHash == null) "OpenChamber srcHash is missing"
    ++ lib.optional (openCodeSourceHash == null) "OpenCode srcHash is missing"
    ++ lib.optional (sherpaSourceHash == null) "sherpa-onnx srcHash is missing"
    ++ lib.optional (bunHash == null) "Bun ${bunVersion} asset hash is missing"
    ++ lib.optional (
      openCodeNodeModulesHash == null
    ) "OpenCode aarch64-darwin nodeModulesHash is missing"
    ++ lib.optional (
      openChamberNodeModulesHash == null
    ) "OpenChamber aarch64-darwin nodeModulesHash is unresolved"
    ++ lib.optional (sherpaWrapperHash == null) "sherpa-onnx-node wrapper hash is missing"
    ++ lib.optional (nodeAddonApiHash == null) "node-addon-api hash is missing";

  commonPassthru = {
    macApp = {
      bundleId = appId;
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
    openchamberBuildGates = unresolvedBuildGates;
    openchamberNativeRuntimePaths = expectedNativeRelativePaths;
    sherpaRuntimeProvenance = {
      addonSourceVersion = sherpaVersion;
      addonSourceCommit = sherpaCommit;
      sherpaNixpkgsVersion = sherpa-onnx.version;
      onnxruntimeNixpkgsVersion = onnxruntime.version;
      managedNixStoreDependencies = true;
      upstreamNpmPrebuiltUsed = false;
      byteIdentityWithUpstreamNpmPrebuiltClaimed = false;
    };
  };

  blockedPackage = stdenvNoCC.mkDerivation {
    inherit pname version;
    dontUnpack = true;
    buildPhase = ''
      echo "OpenChamber is intentionally unbuildable:" >&2
      ${lib.concatMapStringsSep "\n" (
        gate: "echo ${lib.escapeShellArg "- ${gate}"} >&2"
      ) unresolvedBuildGates}
      exit 1
    '';
    installPhase = "exit 1";
    passthru = commonPassthru;
    meta = {
      broken = true;
      description = "Blocked source build of the OpenChamber desktop app";
      homepage = "https://github.com/openchamber/openchamber";
      license = lib.licenses.mit;
      platforms = [ "aarch64-darwin" ];
    };
  };

  openChamberSrc = fetchFromGitHub {
    owner = "openchamber";
    repo = "openchamber";
    rev = selfSource.commit;
    inherit (openChamberSourceHash) hash;
  };

  openCodeSrc = fetchFromGitHub {
    owner = "anomalyco";
    repo = "opencode";
    rev = openCodeCommit;
    inherit (openCodeSourceHash) hash;
  };

  sherpaSrc = fetchFromGitHub {
    owner = "k2-fsa";
    repo = "sherpa-onnx";
    rev = sherpaCommit;
    inherit (sherpaSourceHash) hash;
  };

  sherpaWrapperSrc = fetchurl {
    url = sherpaWrapperUrl;
    inherit (sherpaWrapperHash) hash;
  };

  nodeAddonApiSrc = fetchurl {
    url = nodeAddonApiUrl;
    inherit (nodeAddonApiHash) hash;
  };

  bunSource = fetchurl {
    url = bunUrl;
    inherit (bunHash) hash;
  };

  bunExact = callPackage ./bun.nix {
    inherit bunSource;
    version = bunVersion;
  };

  openChamberNodeModules = callPackage ./node-modules.nix {
    inherit bunVersion cacert version;
    bun = bunExact;
    src = openChamberSrc;
    inherit (openChamberNodeModulesHash) hash;
  };

  openCodeNodeModules = callPackage ./opencode-node-modules.nix {
    inherit bunVersion;
    bun = bunExact;
    src = openCodeSrc;
    version = openCodeVersion;
    inherit (openCodeNodeModulesHash) hash;
  };

  openCode = callPackage ./opencode.nix {
    inherit
      models-dev
      python3
      ripgrep
      sysctl
      ;
    bun = bunExact;
    src = openCodeSrc;
    version = openCodeVersion;
    nodeModules = openCodeNodeModules;
  };

  sherpaNodeAddon = callPackage ./sherpa-node-addon.nix {
    inherit nodeAddonApiSrc;
    src = sherpaSrc;
    version = sherpaVersion;
    wrapperVersion = sherpaWrapperVersion;
    wrapperSrc = sherpaWrapperSrc;
    electronHeaders = electronBuild.headers;
  };

  realPackage = stdenv.mkDerivation {
    inherit pname version;
    src = openChamberSrc;

    nativeBuildInputs = [
      autoconf
      automake
      bunExact
      cctools
      cmake
      libiconv
      makeWrapper
      nodejs_24
      pkg-config
      python3
    ];

    strictDeps = true;
    dontUseCmakeConfigure = true;
    dontStrip = true;

    env = electronBuild.commonEnv // {
      CI = "1";
      CSC_IDENTITY_AUTO_DISCOVERY = "false";
      NODE_OPTIONS = "--max-old-space-size=6144";
      OPENCHAMBER_TARGET_ARCH = "arm64";
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
      } ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD" --component openchamber
    '';

    configurePhase = ''
      runHook preConfigure

      cp -R ${openChamberNodeModules}/. .
      chmod -R u+w node_modules packages/*/node_modules 2>/dev/null || true

      cp -R ${sherpaNodeAddon}/node_modules/. node_modules/
      mkdir -p packages/web/node_modules
      for packageName in sherpa-onnx-node sherpa-onnx-darwin-arm64
      do
        rm -rf "packages/web/node_modules/$packageName"
        ln -s "../../../node_modules/$packageName" "packages/web/node_modules/$packageName"
      done

      patchShebangs node_modules packages/*/node_modules

      runHook postConfigure
    '';

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR/openchamber-build-home"
      mkdir -p "$HOME"
      test "$(bun --version)" = "${bunVersion}"

      bun run --cwd packages/electron build:web-assets

      mkdir -p packages/electron/resources/opencode-cli
      cp -L ${openCode}/bin/opencode packages/electron/resources/opencode-cli/opencode
      chmod 0755 packages/electron/resources/opencode-cli/opencode
      test "$(packages/electron/resources/opencode-cli/opencode --version)" = "${openCodeVersion}"

      bun run --cwd packages/electron bundle:main
      node packages/electron/scripts/rebuild-native.mjs

      ${electronBuild.copyDist}

      for discardedSubtree in ${
        lib.concatMapStringsSep " " lib.escapeShellArg nodePtyDiscardedRuntimeSubtrees
      }
      do
        rm -rf "node_modules/node-pty/$discardedSubtree"
        if [ -e "node_modules/node-pty/$discardedSubtree" ] \
          || [ -L "node_modules/node-pty/$discardedSubtree" ]; then
          echo "failed to remove excluded node-pty runtime subtree: $discardedSubtree" >&2
          exit 1
        fi
      done

      for packageName in ${lib.concatMapStringsSep " " lib.escapeShellArg electronExcludedRuntimePackages}
      do
        ${lib.getExe nodejs_24} -e '
          const fs = require("node:fs");
          const manifestPath = process.argv[1];
          const packageName = process.argv[2];
          const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
          if (!manifest.dependencies || !Object.hasOwn(manifest.dependencies, packageName)) {
            throw new Error("missing expected Electron-excluded dependency: " + packageName);
          }
          delete manifest.dependencies[packageName];
          fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
        ' packages/web/package.json "$packageName"
        if [ ! -e "packages/web/node_modules/$packageName" ] \
          && [ ! -L "packages/web/node_modules/$packageName" ]; then
          echo "missing staged Electron-excluded dependency: $packageName" >&2
          exit 1
        fi
        rm -rf "packages/web/node_modules/$packageName"
      done

      cd packages/electron
      ${electronBuilderExecutable} \
        --mac \
        --arm64 \
        --dir \
        --publish never \
        -c.mac.identity=null \
        -c.mac.notarize=false \
        -c.npmRebuild=false \
        ${electronBuild.electronBuilderConfigFlags}

      cd ../..
      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      appBundle="packages/electron/dist/mac-arm64/${appBundleName}"
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
      /usr/bin/codesign \
        --force \
        --deep \
        --options runtime \
        --sign - \
        --entitlements packages/electron/resources/entitlements.mac.plist \
        "$app"
    '';

    doInstallCheck = true;
    installCheckPhase = ''
            runHook preInstallCheck

            app="$out/Applications/${appBundleName}"
            executable="$app/Contents/MacOS/${appExecutableName}"
            resources="$app/Contents/Resources"
            unpacked="$resources/app.asar.unpacked/node_modules"
            plist="$app/Contents/Info.plist"

            for path in \
              "$app" \
              "$executable" \
              "$resources/app.asar" \
              "$resources/opencode-cli/opencode" \
              "$unpacked/node-pty/build/Release/pty.node" \
              "$unpacked/sherpa-onnx-darwin-arm64/sherpa-onnx.node" \
              "$out/bin/${pname}"
            do
              if [ ! -e "$path" ]; then
                echo "missing required OpenChamber runtime path: $path" >&2
                exit 1
              fi
            done

            for packageName in ${
              lib.concatMapStringsSep " " lib.escapeShellArg electronExcludedRuntimePackages
            }
            do
              if [ -e "$unpacked/$packageName" ] || [ -L "$unpacked/$packageName" ]; then
                echo "Electron-inapplicable dependency escaped into OpenChamber: $packageName" >&2
                exit 1
              fi
            done

            for discardedSubtree in ${
              lib.concatMapStringsSep " " lib.escapeShellArg nodePtyDiscardedRuntimeSubtrees
            }
            do
              if [ -e "$unpacked/node-pty/$discardedSubtree" ] \
                || [ -L "$unpacked/node-pty/$discardedSubtree" ]; then
                echo "excluded node-pty runtime subtree escaped into OpenChamber: $discardedSubtree" >&2
                exit 1
              fi
            done

            nativeInventory="$TMPDIR/openchamber-native-inventory"
            expectedNativeInventory="$TMPDIR/openchamber-expected-native-inventory"
            executableDirectories="$TMPDIR/openchamber-executable-directories"
            rpathRecords="$TMPDIR/openchamber-rpath-records"
            : > "$nativeInventory"
            : > "$rpathRecords"
            printf '%s\n' \
              ${lib.concatMapStringsSep " \\\n        " lib.escapeShellArg expectedNativeRelativePaths} \
              | LC_ALL=C sort -u > "$expectedNativeInventory"
            printf '%s\n' \
              "$app/Contents/MacOS" \
              "$app/Contents/Frameworks/OpenChamber Helper.app/Contents/MacOS" \
              "$app/Contents/Frameworks/OpenChamber Helper (GPU).app/Contents/MacOS" \
              "$app/Contents/Frameworks/OpenChamber Helper (Plugin).app/Contents/MacOS" \
              "$app/Contents/Frameworks/OpenChamber Helper (Renderer).app/Contents/MacOS" \
              "$app/Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers" \
              "$app/Contents/Frameworks/Squirrel.framework/Versions/A/Resources" \
              "$resources/opencode-cli" \
              "$unpacked/node-pty/build/Release" \
              > "$executableDirectories"

            normalizeInsideApp() {
              ${lib.getExe python3} -c '
      from pathlib import Path
      import sys

      root = Path(sys.argv[1]).resolve(strict=True)
      target = Path(sys.argv[2]).resolve(strict=True)
      print(target.relative_to(root).as_posix())
      ' "$app" "$1"
            }

            isInventoriedLocalPath() {
              localPath="$1"
              if ! localRelative="$(normalizeInsideApp "$localPath" 2>/dev/null)"; then
                return 1
              fi
              grep -Fqx "$localRelative" "$nativeInventory"
            }

            while IFS= read -r -d $'\0' candidate
            do
              description="$(/usr/bin/file -b "$candidate")"
              case "$description" in
                ${lib.concatStringsSep "|" forbiddenRuntimeBinaryFormats})
                  echo "OpenChamber contains a foreign binary format: $candidate ($description)" >&2
                  exit 1
                  ;;
              esac
              case "$description" in
                *Mach-O*)
                  if ! relativeCandidate="$(normalizeInsideApp "$candidate" 2>/dev/null)"; then
                    echo "OpenChamber native runtime escapes its app bundle: $candidate" >&2
                    exit 1
                  fi
                  if ! grep -Fqx "$relativeCandidate" "$expectedNativeInventory"; then
                    echo "unexpected OpenChamber Mach-O runtime: $relativeCandidate" >&2
                    exit 1
                  fi
                  printf '%s\n' "$relativeCandidate" >> "$nativeInventory"
                  architectures="$(/usr/bin/lipo -archs "$candidate")"
                  if [ "$architectures" != arm64 ]; then
                    echo "OpenChamber runtime is not arm64-only: $candidate ($architectures)" >&2
                    exit 1
                  fi
                  ;;
                *)
                  case "$candidate" in
                    *.node|*.dylib|*.dylib.*)
                      echo "OpenChamber native-looking file is not Mach-O: $candidate" >&2
                      exit 1
                      ;;
                  esac
                  ;;
              esac
            done < <(find "$app" -type f -print0)

            LC_ALL=C sort -u "$nativeInventory" -o "$nativeInventory"
            if ! cmp -s "$expectedNativeInventory" "$nativeInventory"; then
              echo "OpenChamber native runtime inventory differs from its exact allowlist:" >&2
              diff -u "$expectedNativeInventory" "$nativeInventory" >&2 || true
              exit 1
            fi

            while IFS= read -r -d $'\0' linkCandidate
            do
              if ! linkTarget="$(normalizeInsideApp "$linkCandidate" 2>/dev/null)"; then
                echo "OpenChamber bundle symlink is dangling or escapes the app: $linkCandidate" >&2
                exit 1
              fi
              description="$(/usr/bin/file -bL "$linkCandidate")"
              case "$description" in
                ${lib.concatStringsSep "|" forbiddenRuntimeBinaryFormats})
                  echo "OpenChamber symlink resolves to a foreign binary format: $linkCandidate ($description)" >&2
                  exit 1
                  ;;
              esac
              case "$description" in
                *Mach-O*)
                  if ! grep -Fqx "$linkTarget" "$nativeInventory"; then
                    echo "OpenChamber native symlink target is not inventoried: $linkCandidate -> $linkTarget" >&2
                    exit 1
                  fi
                  ;;
                *)
                  case "$linkCandidate" in
                    *.node|*.dylib|*.dylib.*)
                      echo "OpenChamber native-looking symlink does not resolve to Mach-O: $linkCandidate" >&2
                      exit 1
                      ;;
                  esac
                  ;;
              esac
            done < <(find "$app" -type l -print0)

            while IFS= read -r relativeCandidate
            do
              candidate="$app/$relativeCandidate"
              /usr/bin/otool -l "$candidate" \
                | awk '
                    $1 == "cmd" && $2 == "LC_RPATH" { in_rpath = 1; next }
                    in_rpath && $1 == "path" { print $2; in_rpath = 0 }
                  ' \
                | while IFS= read -r runtimeRpath
                  do
                    case "$runtimeRpath" in
                      @loader_path|@loader_path/*|@executable_path|@executable_path/*)
                        printf '%s\t%s\n' "$candidate" "$runtimeRpath" >> "$rpathRecords"
                        ;;
                      *)
                        echo "OpenChamber runtime has an external or unsupported LC_RPATH: $candidate -> $runtimeRpath" >&2
                        exit 1
                        ;;
                    esac
                  done
            done < "$nativeInventory"

            while IFS= read -r relativeCandidate
            do
              candidate="$app/$relativeCandidate"
              candidateDirectory="$(dirname "$candidate")"
              installName="$(/usr/bin/otool -D "$candidate" 2>/dev/null | tail -n +2 | head -n 1 || true)"
              /usr/bin/otool -L "$candidate" \
                | tail -n +2 \
                | sed 's/^[[:space:]]*//; s/ (compatibility version.*$//' \
                | while IFS= read -r dependency
                  do
                    if [ -n "$installName" ] && [ "$dependency" = "$installName" ]; then
                      continue
                    fi
                    case "$dependency" in
                      /System/Library/*|/usr/lib/*)
                        ;;
                      @loader_path/*)
                        linkedPath="$candidateDirectory/''${dependency#@loader_path/}"
                        if ! isInventoriedLocalPath "$linkedPath"; then
                          echo "unresolved @loader_path dependency: $candidate -> $dependency" >&2
                          exit 1
                        fi
                        ;;
                      @executable_path/*)
                        executableDependencyResolved=false
                        while IFS= read -r executableDirectory
                        do
                          linkedPath="$executableDirectory/''${dependency#@executable_path/}"
                          if isInventoriedLocalPath "$linkedPath"; then
                            executableDependencyResolved=true
                            break
                          fi
                        done < "$executableDirectories"
                        if [ "$executableDependencyResolved" != true ]; then
                          echo "unresolved @executable_path dependency: $candidate -> $dependency" >&2
                          exit 1
                        fi
                        ;;
                      @rpath/*)
                        rpathDependencyResolved=false
                        while IFS=$'\t' read -r rpathOwner runtimeRpath
                        do
                          case "$runtimeRpath" in
                            @loader_path)
                              rpathBase="$(dirname "$rpathOwner")"
                              if isInventoriedLocalPath "$rpathBase/''${dependency#@rpath/}"; then
                                rpathDependencyResolved=true
                                break
                              fi
                              ;;
                            @loader_path/*)
                              rpathBase="$(dirname "$rpathOwner")/''${runtimeRpath#@loader_path/}"
                              if isInventoriedLocalPath "$rpathBase/''${dependency#@rpath/}"; then
                                rpathDependencyResolved=true
                                break
                              fi
                              ;;
                            @executable_path|@executable_path/*)
                              while IFS= read -r executableDirectory
                              do
                                case "$runtimeRpath" in
                                  @executable_path)
                                    rpathBase="$executableDirectory"
                                    ;;
                                  *)
                                    rpathBase="$executableDirectory/''${runtimeRpath#@executable_path/}"
                                    ;;
                                esac
                                if isInventoriedLocalPath "$rpathBase/''${dependency#@rpath/}"; then
                                  rpathDependencyResolved=true
                                  break
                                fi
                              done < "$executableDirectories"
                              if [ "$rpathDependencyResolved" = true ]; then
                                break
                              fi
                              ;;
                          esac
                        done < "$rpathRecords"
                        if [ "$rpathDependencyResolved" != true ]; then
                          echo "unresolved @rpath dependency: $candidate -> $dependency" >&2
                          exit 1
                        fi
                        ;;
                      /nix/store/*)
                        case "$relativeCandidate" in
                          Contents/Resources/app.asar.unpacked/node_modules/sherpa-onnx-darwin-arm64/*)
                            if [ ! -e "$dependency" ]; then
                              echo "missing managed OpenChamber dependency: $candidate -> $dependency" >&2
                              exit 1
                            fi
                            dependencyDescription="$(/usr/bin/file -b "$dependency")"
                            case "$dependencyDescription" in
                              *Mach-O*) ;;
                              *)
                                echo "managed OpenChamber dependency is not Mach-O: $candidate -> $dependency" >&2
                                exit 1
                                ;;
                            esac
                            dependencyArchitectures="$(/usr/bin/lipo -archs "$dependency")"
                            if [ "$dependencyArchitectures" != arm64 ]; then
                              echo "managed OpenChamber dependency is not arm64-only: $candidate -> $dependency ($dependencyArchitectures)" >&2
                              exit 1
                            fi
                            ;;
                          *)
                            echo "unexpected managed OpenChamber dependency: $candidate -> $dependency" >&2
                            exit 1
                            ;;
                        esac
                        ;;
                      *)
                        echo "external or unsupported OpenChamber dependency: $candidate -> $dependency" >&2
                        exit 1
                        ;;
                    esac
                  done
            done < "$nativeInventory"

            test "$($resources/opencode-cli/opencode --version)" = "${openCodeVersion}"
            test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")" = "${appId}"
            test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = "${version}"
            test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist")" = "${appExecutableName}"

            DYLD_LIBRARY_PATH="$unpacked/sherpa-onnx-darwin-arm64" \
              ELECTRON_RUN_AS_NODE=1 \
              "$executable" \
              -e '
                const assert = require("node:assert/strict");
                const app = process.argv[1];
                const expectedElectronVersion = process.argv[2];
                assert.equal(process.versions.electron, expectedElectronVersion);
                assert.equal(process.arch, "arm64");
                const pty = require(app + "/Contents/Resources/app.asar/node_modules/node-pty");
                assert.equal(typeof pty.spawn, "function");
                const sherpa = require(app + "/Contents/Resources/app.asar/node_modules/sherpa-onnx-node");
                assert.equal(typeof sherpa, "object");
                assert.ok(Object.keys(sherpa).length > 0);
              ' \
              "$app" "${electronVersion}"

            rm -rf "$TMPDIR/openchamber-asar"
            ${lib.getExe nodejs_24} ${asarExecutable} extract "$resources/app.asar" "$TMPDIR/openchamber-asar"
            for packageName in ${
              lib.concatMapStringsSep " " lib.escapeShellArg electronExcludedRuntimePackages
            }
            do
              if [ -e "$TMPDIR/openchamber-asar/node_modules/$packageName" ] \
                || [ -L "$TMPDIR/openchamber-asar/node_modules/$packageName" ]; then
                echo "Electron-inapplicable dependency escaped into OpenChamber ASAR: $packageName" >&2
                exit 1
              fi
            done
            for discardedSubtree in ${
              lib.concatMapStringsSep " " lib.escapeShellArg nodePtyDiscardedRuntimeSubtrees
            }
            do
              if [ -e "$TMPDIR/openchamber-asar/node_modules/node-pty/$discardedSubtree" ] \
                || [ -L "$TMPDIR/openchamber-asar/node_modules/node-pty/$discardedSubtree" ]; then
                echo "excluded node-pty runtime subtree escaped into OpenChamber ASAR: $discardedSubtree" >&2
                exit 1
              fi
            done
            while IFS= read -r -d $'\0' candidate
            do
              description="$(/usr/bin/file -b "$candidate")"
              case "$description" in
                ${lib.concatStringsSep "|" forbiddenRuntimeBinaryFormats})
                  echo "OpenChamber ASAR contains a foreign binary format: $candidate ($description)" >&2
                  exit 1
                  ;;
              esac
            done < <(find "$TMPDIR/openchamber-asar" -type f -print0)
            # This validates compatibility metadata, not byte identity with the npm
            # prebuilt. passthru.sherpaRuntimeProvenance records the linked inputs.
            sherpaPackageVersion="$(${lib.getExe nodejs_24} -p 'require(process.argv[1]).version' "$TMPDIR/openchamber-asar/node_modules/sherpa-onnx-darwin-arm64/package.json")"
            test "$sherpaPackageVersion" = "${sherpaVersion}"
            grep -R -Fq 'Updates are managed by Nix.' "$TMPDIR/openchamber-asar"
            /usr/bin/codesign --verify --deep --strict "$app"

            runHook postInstallCheck
    '';

    passthru = commonPassthru // {
      inherit
        bunExact
        electronBuild
        openChamberNodeModules
        openCode
        openCodeNodeModules
        sherpaNodeAddon
        ;
    };

    meta = {
      description = "Open-source AI coding agent desktop app";
      homepage = "https://github.com/openchamber/openchamber";
      license = lib.licenses.mit;
      mainProgram = pname;
      platforms = [ "aarch64-darwin" ];
      sourceProvenance = with lib.sourceTypes; [
        fromSource
        binaryNativeCode
      ];
    };
  };
in
if unresolvedBuildGates == [ ] then realPackage else blockedPackage
