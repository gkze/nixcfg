{
  bun,
  cacert,
  inputs,
  lib,
  makeWrapper,
  nixcfgElectron,
  nodejs,
  outputs,
  python3,
  stdenv,
  ...
}:
let
  pname = "t3code-desktop";
  appName = "T3 Code (Alpha)";
  appBundleName = "${appName}.app";
  appId = "com.t3tools.t3code";
  appProtocolScheme = "t3";
  electronBuilderVersion = "26.8.1";

  shared = import ../t3code/_shared.nix {
    inherit
      bun
      cacert
      inputs
      lib
      nodejs
      outputs
      stdenv
      ;
    sourceHashPackageName = "t3code-workspace";
  };
  inherit (shared)
    src
    version
    workspaceBuild
    ;
  inherit (stdenv.hostPlatform) system;

  serverPackageJson = builtins.fromJSON (builtins.readFile "${src}/apps/server/package.json");
  desktopPackageJson = builtins.fromJSON (builtins.readFile "${src}/apps/desktop/package.json");
  appVersion = serverPackageJson.version;
  electronVersion = desktopPackageJson.dependencies.electron;
  electronRuntime = nixcfgElectron.runtimeFor electronVersion;
  electronRuntimeVersion = electronRuntime.version;
  electronHeaders = electronRuntime.passthru.headers;
  electronDist = electronRuntime.passthru.dist;
  versionSyncCheck =
    if serverPackageJson.version == desktopPackageJson.version then
      true
    else
      throw ''
        packages/t3code-desktop/default.nix expected matching upstream versions,
        got server ${serverPackageJson.version} and desktop ${desktopPackageJson.version}
      '';
  t3codeCommitHash = outputs.lib.flakeLock.t3code.locked.rev or "";

  node_modules = stdenv.mkDerivation {
    pname = "${pname}-node_modules";
    inherit version src;

    nativeBuildInputs = [
      bun
      cacert
      python3
    ];

    dontPatchShebangs = true;
    dontFixup = true;

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR/home"
      mkdir -p "$HOME"
      export SSL_CERT_FILE="${cacert}/etc/ssl/certs/ca-bundle.crt"
      export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE"
      export BUN_INSTALL_CACHE_DIR="$TMPDIR/.bun-cache"

      ${lib.getExe python3} ${./render_runtime_package_json.py} \
        ${src} \
        --electron-builder-version ${lib.escapeShellArg electronBuilderVersion} \
        --commit-hash ${lib.escapeShellArg t3codeCommitHash} \
        --output package.json
      cp ${./bun.lock} bun.lock

      bun install \
        --frozen-lockfile \
        --ignore-scripts \
        --no-progress

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      mkdir -p "$out"
      cp package.json "$out/package.json"
      cp bun.lock "$out/bun.lock"
      cp -R node_modules "$out/node_modules"

      runHook postInstall
    '';

    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = outputs.lib.sourceHashForPlatform pname "nodeModulesHash" system;
  };
in
assert versionSyncCheck;
stdenv.mkDerivation {
  inherit
    pname
    version
    src
    node_modules
    ;

  nativeBuildInputs = [
    bun
    makeWrapper
    nodejs
    python3
  ];

  strictDeps = true;

  env = {
    CI = "1";
    CSC_IDENTITY_AUTO_DISCOVERY = "false";
    ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
    NODE_OPTIONS = "--max-old-space-size=6144";
  };

  dontUnpack = true;

  buildPhase = ''
    runHook preBuild

    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    export SSL_CERT_FILE="${cacert}/etc/ssl/certs/ca-bundle.crt"
    export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE"

    mkdir -p apps/desktop apps/server
    cp -R ${workspaceBuild}/apps/desktop/dist-electron apps/desktop/dist-electron
    cp -R ${workspaceBuild}/apps/desktop/resources apps/desktop/resources
    cp -R ${workspaceBuild}/apps/server/dist apps/server/dist
    cp -R ${node_modules}/node_modules ./node_modules
    cp ${node_modules}/package.json ./package.json
    chmod -R u+w apps node_modules package.json

    patchShebangs node_modules

    electronDistDir="$PWD/electron-dist"
    mkdir -p "$electronDistDir"
    cp -R ${electronDist}/. "$electronDistDir"/
    chmod -R u+w "$electronDistDir"

    ./node_modules/.bin/electron-builder \
      --mac dir \
      --publish never \
      -c.appId=${lib.escapeShellArg appId} \
      -c.productName=${lib.escapeShellArg appName} \
      -c.directories.buildResources=apps/desktop/resources \
      -c.mac.icon=icon.icns \
      -c.mac.category=public.app-category.developer-tools \
      -c.mac.identity=null \
      -c.mac.hardenedRuntime=false \
      -c.mac.notarize=false \
      -c.electronVersion=${lib.escapeShellArg electronRuntimeVersion} \
      -c.electronDist="$electronDistDir"

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    appBundle="dist/mac-arm64/${appBundleName}"
    if [ ! -d "$appBundle" ]; then
      echo "failed to locate packaged ${appBundleName} in dist/mac-arm64" >&2
      exit 1
    fi

    mkdir -p "$out/Applications" "$out/bin"
    cp -R "$appBundle" "$out/Applications/${appBundleName}"

    ${lib.getExe python3} ${./patch_info_plist.py} \
      "$out/Applications/${appBundleName}/Contents/Info.plist" \
      --app-name ${lib.escapeShellArg appName} \
      --bundle-id ${lib.escapeShellArg appId} \
      --version ${lib.escapeShellArg appVersion} \
      --icon-file icon.icns \
      --url-scheme ${lib.escapeShellArg appProtocolScheme}

    makeWrapper \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" \
      "$out/bin/${pname}"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    for path in \
      "$out/Applications/${appBundleName}" \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" \
      "$out/bin/${pname}"
    do
      if [ ! -e "$path" ]; then
        echo "missing required runtime path: $path" >&2
        exit 1
      fi
    done

    if [ -L "$out/bin/${pname}" ]; then
      echo "expected $out/bin/${pname} to be a launcher script, not a symlink" >&2
      exit 1
    fi

    runHook postInstallCheck
  '';

  passthru = {
    inherit
      electronDist
      electronHeaders
      electronRuntime
      electronRuntimeVersion
      electronVersion
      ;

    macApp = {
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
  };

  meta = with lib; {
    description = "T3 Code desktop app";
    homepage = "https://github.com/pingdotgg/t3code";
    license = licenses.mit;
    mainProgram = pname;
    platforms = [ "aarch64-darwin" ];
  };
}
