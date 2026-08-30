{
  cctools,
  hermes-agent,
  inputs,
  lib,
  makeWrapper,
  nixcfgElectron,
  python3,
  selfSource,
  stdenv,
  ...
}:
let
  pname = "hermes-desktop";
  appName = "Hermes";
  appBundleName = "${appName}.app";
  appExecutableName = appName;
  appId = "com.nousresearch.hermes";
  appProtocolScheme = "hermes";

  src = inputs.hermes-agent;
  sourceRevision = src.rev or null;
  desktopPackageJson = builtins.fromJSON (builtins.readFile "${src}/apps/desktop/package.json");
  inherit (desktopPackageJson) version;
  electronVersion = desktopPackageJson.devDependencies.electron;
  electronBuild = nixcfgElectron.sourceBuildFor electronVersion;
  electronRuntimeVersion = electronBuild.runtimeVersion;
  hermesExecutable = lib.getExe hermes-agent;
  hermesVersion = hermes-agent.version;
  inherit (hermes-agent.passthru) hermesNpmLib;

  sourceRevisionCheck =
    if sourceRevision == selfSource.commit then
      true
    else
      throw ''
        packages/hermes-desktop/default.nix source revision ${toString sourceRevision}
        does not match sources.json commit ${selfSource.commit}
      '';

  electronVersionCheck =
    if electronRuntimeVersion == electronVersion then
      true
    else
      throw ''
        packages/hermes-desktop/default.nix needs an exact Electron runtime
        matching the selected source, but got ${electronVersion}/${electronRuntimeVersion}
      '';
in
assert stdenv.hostPlatform.system == "aarch64-darwin";
assert sourceRevisionCheck;
assert electronVersionCheck;
hermesNpmLib.buildNpmPackage {
  inherit
    pname
    version
    ;

  dirs = [
    "apps/desktop"
    "apps/shared"
  ];

  nativeBuildInputs = [
    cctools
    makeWrapper
    python3
  ];

  strictDeps = true;
  dontNpmBuild = true;
  dontStrip = true;

  env = (builtins.removeAttrs electronBuild.commonEnv [ "ELECTRON_SKIP_BINARY_DOWNLOAD" ]) // {
    CI = "1";
    CSC_IDENTITY_AUTO_DISCOVERY = "false";
    ELECTRON_IS_DEV = "0";
    HERMES_DESKTOP_HERMES = hermesExecutable;
    NODE_OPTIONS = "--max-old-space-size=6144";
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
    } ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD" ${lib.escapeShellArg hermesExecutable} ${lib.escapeShellArg hermesVersion}
  '';

  buildPhase = ''
    runHook preBuild

    mkdir -p apps/desktop/build
    patchShebangs apps/desktop/scripts

    pushd apps/desktop
    printf '%s\n' \
      '{"schemaVersion":1,"commit":"${selfSource.commit}","branch":"nix","dirty":false,"source":"nix"}' \
      > build/install-stamp.json
    npm exec -- tsc -b
    npm exec -- vite build
    node scripts/bundle-electron-main.mjs

    ${lib.getExe hermesNpmLib.node-gyp} rebuild \
      --directory=../../node_modules/node-pty \
      --build-from-source \
      --runtime=electron \
      --target=${electronVersion} \
      --nodedir=${electronBuild.headers} \
      --disturl="" \
      --offline

    node scripts/stage-native-deps.mjs darwin arm64
    npm run postbuild

    ${electronBuild.copyDist}
    # Keep npmRebuild true so upstream beforeBuild runs and returns false, which
    # marks the pre-bundled node_modules payload as externally managed. Setting
    # the option false returns before that hook and makes electron-builder scan
    # the full workspace dependency graph.
    node scripts/run-electron-builder.mjs \
      --mac \
      --arm64 \
      --dir \
      --publish never \
      -c.mac.identity=null \
      -c.npmRebuild=true \
      ${electronBuild.electronBuilderConfigFlags}
    popd

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    appBundle="apps/desktop/release/mac-arm64/${appBundleName}"
    if [ ! -d "$appBundle" ]; then
      echo "failed to locate packaged ${appBundleName} at $appBundle" >&2
      exit 1
    fi

    mkdir -p "$out/Applications" "$out/bin"
    cp -R "$appBundle" "$out/Applications/${appBundleName}"
    makeWrapper \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appExecutableName}" \
      "$out/bin/${pname}" \
      --set HERMES_DESKTOP_HERMES ${lib.escapeShellArg hermesExecutable}

    runHook postInstall
  '';

  postFixup = ''
    /usr/bin/xattr -cr "$out/Applications/${appBundleName}"
    /usr/bin/codesign --force --deep --sign - "$out/Applications/${appBundleName}"
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    app="$out/Applications/${appBundleName}"
    executable="$app/Contents/MacOS/${appExecutableName}"
    resources="$app/Contents/Resources"
    mainBundle="$resources/app.asar.unpacked/dist/electron-main.mjs"
    managedVersionDeclaration=${lib.escapeShellArg "var NIX_MANAGED_HERMES_VERSION = ${builtins.toJSON hermesVersion};"}
    plist="$app/Contents/Info.plist"

    for path in \
      "$app" \
      "$executable" \
      "$resources/app.asar" \
      "$resources/app.asar.unpacked" \
      "$mainBundle" \
      "$out/bin/${pname}"
    do
      if [ ! -e "$path" ]; then
        echo "missing required Hermes Desktop runtime path: $path" >&2
        exit 1
      fi
    done

    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")" = "${appId}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = "${version}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist")" = "${appExecutableName}"
    /usr/libexec/PlistBuddy -c 'Print :CFBundleURLTypes' "$plist" | grep -Fq "${appProtocolScheme}"
    /usr/bin/file "$executable" | grep -Fq 'Mach-O 64-bit executable arm64'
    /usr/bin/codesign --verify --deep --strict "$app"

    grep -R -Fq ${lib.escapeShellArg hermesExecutable} "$resources"
    managedVersionCount="$(grep -Fxc -- "$managedVersionDeclaration" "$mainBundle" || true)"
    if [ "$managedVersionCount" -ne 1 ]; then
      echo "expected one exact Nix-managed Hermes version declaration, found $managedVersionCount" >&2
      exit 1
    fi
    grep -R -Fq 'Updates are managed by Nix.' "$resources"

    runHook postInstallCheck
  '';

  passthru = {
    packageJsonPath = "apps/desktop/package.json";
    macApp = {
      bundleId = appId;
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "symlink";
    };
  };

  meta = {
    description = "Native desktop shell for Hermes Agent";
    homepage = "https://github.com/NousResearch/hermes-agent";
    license = lib.licenses.mit;
    mainProgram = pname;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
  };
}
