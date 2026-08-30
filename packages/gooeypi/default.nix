{
  autoconf,
  automake,
  cctools,
  cmake,
  fetchFromGitHub,
  fetchNpmDeps,
  fetchurl,
  lib,
  libiconv,
  makeWrapper,
  nixcfgElectron,
  nodejs_24,
  npmHooks,
  outputs,
  pkg-config,
  python3,
  selfSource,
  stdenv,
  stdenvNoCC,
  ...
}:
let
  pname = "gooeypi";
  appName = "GooeyPi";
  appBundleName = "${appName}.app";
  appExecutableName = appName;
  appId = "app.gooeypi.desktop";
  npmCliVersion = selfSource.pins.npmVersion;
  npmCliUrl = "https://registry.npmjs.org/npm/-/npm-${npmCliVersion}.tgz";
  inherit (selfSource) electronVersion version;

  npmCliSource = lib.findFirst (
    entry: entry.hashType == "sha256" && (entry.url or null) == npmCliUrl
  ) null selfSource.hashes;

  src = fetchFromGitHub {
    owner = "am-will";
    repo = "gooey-pi";
    rev = selfSource.commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  npmDeps = fetchNpmDeps {
    name = "${pname}-${version}-npm-deps";
    inherit src;
    hash = outputs.lib.sourceHash pname "npmDepsHash";
  };

  nodejs = nodejs_24;
  npmCli = stdenvNoCC.mkDerivation {
    pname = "npm-cli";
    version = npmCliVersion;

    src = fetchurl {
      inherit (npmCliSource) hash url;
    };

    nativeBuildInputs = [ makeWrapper ];
    dontBuild = true;

    installPhase = ''
      runHook preInstall

      mkdir -p "$out/bin" "$out/lib/node_modules/npm"
      cp -R . "$out/lib/node_modules/npm"
      makeWrapper \
        ${lib.getExe nodejs} \
        "$out/bin/npm" \
        --add-flags "$out/lib/node_modules/npm/bin/npm-cli.js"
      makeWrapper \
        ${lib.getExe nodejs} \
        "$out/bin/npx" \
        --add-flags "$out/lib/node_modules/npm/bin/npx-cli.js"
      test "$("$out/bin/npm" --version)" = "${npmCliVersion}"

      runHook postInstall
    '';
  };
  electronBuild = nixcfgElectron.sourceBuildFor electronVersion;
in
assert npmCliSource != null;
assert stdenv.hostPlatform.system == "aarch64-darwin";
stdenv.mkDerivation {
  inherit
    npmDeps
    pname
    src
    version
    ;

  nativeBuildInputs = [
    autoconf
    automake
    cctools
    cmake
    libiconv
    makeWrapper
    nodejs
    npmCli
    npmHooks.npmConfigHook
    pkg-config
    python3
  ];

  strictDeps = true;
  # CMake supports native addon rebuilds; the app root is not a CMake project.
  dontUseCmakeConfigure = true;
  dontStrip = true;

  env = electronBuild.commonEnv // {
    CI = "1";
    CSC_IDENTITY_AUTO_DISCOVERY = "false";
    NODE_OPTIONS = "--max-old-space-size=6144";
    npm_config_build_from_source = "true";
    npm_config_engine_strict = "true";
  };

  # npmConfigHook installs the pinned lock closure without scripts. Native
  # modules are rebuilt explicitly below for the exact Electron ABI. npm 12
  # writes cache metadata during install, so copy the fixed-output cache first.
  makeCacheWritable = true;
  npmRebuildFlags = [ "--ignore-scripts" ];

  prePatch = ''
    export PATH="${npmCli}/bin:$PATH"
    test "$(npm --version)" = "${npmCliVersion}"
  '';

  postPatch = ''
    ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD"
  '';

  buildPhase = ''
    runHook preBuild

    export HOME="$TMPDIR/gooeypi-home"
    mkdir -p "$HOME"

    npm run build

    npm exec -- electron-rebuild \
      -f \
      -v ${electronVersion} \
      --only=node-pty,zeromq

    ${electronBuild.copyDist}

    codesignPath="$TMPDIR/gooeypi-codesign"
    mkdir -p "$codesignPath"
    ln -s /usr/bin/codesign "$codesignPath/codesign"

    PATH="$codesignPath:$PATH" npm exec -- electron-builder \
      --mac \
      --arm64 \
      --dir \
      --publish never \
      -c.mac.identity=null \
      -c.mac.notarize=false \
      -c.npmRebuild=false \
      ${electronBuild.electronBuilderConfigFlags}

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    appBundle="release/mac-arm64/${appBundleName}"
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
      --entitlements build/entitlements.mac.plist \
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
      "$unpacked/node-pty/build/Release/pty.node" \
      "$unpacked/node-pty/build/Release/spawn-helper" \
      "$out/bin/${pname}"
    do
      if [ ! -e "$path" ]; then
        echo "missing required GooeyPi runtime path: $path" >&2
        exit 1
      fi
    done

    mapfile -t zeromqAddons < <(
      find \
        "$unpacked/zeromq/build/darwin/arm64/node" \
        -type f \
        -path '*-Release/addon.node'
    )
    if [ "''${#zeromqAddons[@]}" -ne 1 ]; then
      echo "expected one arm64 ZeroMQ addon, found ''${#zeromqAddons[@]}" >&2
      exit 1
    fi

    for binary in \
      "$executable" \
      "$unpacked/node-pty/build/Release/pty.node" \
      "$unpacked/node-pty/build/Release/spawn-helper" \
      "''${zeromqAddons[0]}"
    do
      /usr/bin/lipo "$binary" -verify_arch arm64
    done

    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")" = "${appId}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = "${version}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist")" = "${appExecutableName}"

    npm exec -- asar list "$resources/app.asar" > "$TMPDIR/gooeypi-asar-files"
    grep -Fxq '/out/main/index.js' "$TMPDIR/gooeypi-asar-files"
    grep -Fxq '/out/preload/index.js' "$TMPDIR/gooeypi-asar-files"
    grep -Fxq '/out/renderer/index.html' "$TMPDIR/gooeypi-asar-files"
    grep -Fxq '/node_modules/node-pty/lib/index.js' "$TMPDIR/gooeypi-asar-files"
    grep -Fxq '/node_modules/zeromq/lib/index.js' "$TMPDIR/gooeypi-asar-files"

    FORCE_COLOR=0 node node_modules/@electron/fuses/dist/bin.js \
      read --app "$app" > "$TMPDIR/gooeypi-fuses"
    grep -Fxq '  RunAsNode is Disabled' "$TMPDIR/gooeypi-fuses"
    grep -Fxq '  EnableCookieEncryption is Enabled' "$TMPDIR/gooeypi-fuses"
    grep -Fxq '  EnableNodeOptionsEnvironmentVariable is Disabled' "$TMPDIR/gooeypi-fuses"
    grep -Fxq '  EnableNodeCliInspectArguments is Disabled' "$TMPDIR/gooeypi-fuses"
    grep -Fxq '  EnableEmbeddedAsarIntegrityValidation is Enabled' "$TMPDIR/gooeypi-fuses"
    grep -Fxq '  OnlyLoadAppFromAsar is Enabled' "$TMPDIR/gooeypi-fuses"
    grep -Fxq '  LoadBrowserProcessSpecificV8Snapshot is Disabled' "$TMPDIR/gooeypi-fuses"
    grep -Fxq '  GrantFileProtocolExtraPrivileges is Disabled' "$TMPDIR/gooeypi-fuses"
    grep -Fxq '  WasmTrapHandlers is Enabled' "$TMPDIR/gooeypi-fuses"

    /usr/bin/codesign --verify --deep --strict "$app"
    /usr/bin/codesign -d --entitlements - "$app" 2>&1 \
      | grep -Fq 'com.apple.security.device.audio-input'

    runHook postInstallCheck
  '';

  passthru = {
    inherit
      electronBuild
      npmCli
      npmDeps
      ;
    macApp = {
      bundleId = appId;
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
  };

  meta = {
    description = "Desktop workspace for Pi, OMP, and Prime Agent";
    homepage = "https://github.com/am-will/gooey-pi";
    license = lib.licenses.mit;
    mainProgram = pname;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
  };
}
