{
  fetchPnpmDeps ? null,
  goose-cli,
  lib,
  makeWrapper,
  nixcfgElectron,
  nodejs_24,
  outputs,
  pnpmConfigHook,
  pnpm_10,
  python3,
  stdenv,
  ...
}:
let
  pname = "goose-desktop";
  appName = "Goose";
  appBundleName = "${appName}.app";
  appExecutableName = appName;
  appId = "io.github.block.Goose";
  appProtocolScheme = "goose";

  inherit (stdenv.hostPlatform) system;
  inherit (goose-cli.passthru) version;
  src = goose-cli.passthru.patchedSrc;
  nodejs = nodejs_24;
  pnpm = pnpm_10.override { inherit nodejs; };
  slib = outputs.lib;

  desktopPackageJson = builtins.fromJSON (builtins.readFile "${src}/ui/desktop/package.json");
  desktopPackageVersion = desktopPackageJson.version;
  electronVersion = desktopPackageJson.devDependencies.electron;
  electronRuntime = nixcfgElectron.runtimeFor electronVersion;
  electronRuntimeVersion = electronRuntime.version;
  electronDist = electronRuntime.passthru.dist;
  electronZip = electronRuntime.src;
  electronPlatform =
    if stdenv.hostPlatform.isDarwin then
      "darwin"
    else
      throw "packages/goose-desktop/default.nix only supports Electron desktop builds on Darwin";
  electronArch =
    if stdenv.hostPlatform.isAarch64 then
      "arm64"
    else if stdenv.hostPlatform.isx86_64 then
      "x64"
    else
      throw "packages/goose-desktop/default.nix: unsupported Electron architecture ${system}";
  electronZipName = "electron-v${electronVersion}-${electronPlatform}-${electronArch}.zip";

  desktopPackageVersionCheck =
    if desktopPackageVersion == version then
      true
    else
      throw ''
        packages/goose-desktop/default.nix has desktop version ${desktopPackageVersion},
        expected ${version} from goose-cli
      '';

  electronRuntimeVersionCheck =
    if electronRuntimeVersion == electronVersion then
      true
    else
      throw ''
        packages/goose-desktop/default.nix needs Electron ${electronVersion},
        but the selected runtime is ${electronRuntimeVersion}; add the exact runtime to nixcfgElectron
      '';

  pnpmDeps =
    let
      args = {
        inherit
          pname
          pnpm
          src
          version
          ;
        sourceRoot = "${src.name}/ui";
        fetcherVersion = 3;
        hash = slib.sourceHashForPlatform pname "nodeModulesHash" system;
      };
    in
    if fetchPnpmDeps != null then fetchPnpmDeps args else pnpm.fetchDeps args;
in
assert desktopPackageVersionCheck;
assert electronRuntimeVersionCheck;
stdenv.mkDerivation {
  inherit
    pname
    pnpmDeps
    src
    version
    ;

  sourceRoot = "${src.name}/ui";

  nativeBuildInputs = [
    makeWrapper
    nodejs
    pnpm
    pnpmConfigHook
    python3
  ];

  strictDeps = true;

  env = {
    CI = "1";
    CSC_IDENTITY_AUTO_DISCOVERY = "false";
    ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
    GOOSE_BUNDLE_NAME = appName;
    NODE_OPTIONS = "--max-old-space-size=6144";
    npm_config_manage_package_manager_versions = "false";
  };

  postPatch = ''
    ${lib.getExe python3} - <<'PY'
    from pathlib import Path

    path = Path("desktop/forge.config.ts")
    text = path.read_text()
    text = text.replace(
        "  icon: 'src/images/icon',",
        """  icon: 'src/images/icon',
      electronVersion: process.env.ELECTRON_VERSION,
      electronZipDir: process.env.ELECTRON_ZIP_DIR,""",
    )
    path.write_text(text)
    PY

    substituteInPlace desktop/src/updates.ts \
      --replace-fail "export const UPDATES_ENABLED = true;" \
      "export const UPDATES_ENABLED = false;"
  '';

  buildPhase = ''
    runHook preBuild

    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    pnpm config set manage-package-manager-versions false

    install -m755 ${goose-cli.passthru.gooseServerDrv}/bin/goosed desktop/src/bin/goosed

    electronDistDir="$PWD/electron-dist"
    mkdir -p "$electronDistDir"
    cp -R ${electronDist}/. "$electronDistDir"/
    chmod -R u+w "$electronDistDir"
    export ELECTRON_OVERRIDE_DIST_PATH="$electronDistDir"
    export ELECTRON_VERSION="${electronVersion}"

    electronZipDir="$PWD/electron-zip-dir"
    mkdir -p "$electronZipDir"
    ln -s ${electronZip} "$electronZipDir/${electronZipName}"
    export ELECTRON_ZIP_DIR="$electronZipDir"

    pnpm --filter @aaif/goose-sdk run build
    node desktop/scripts/prepare-platform-binaries.js
    PATH="/usr/bin:$PATH" pnpm --dir desktop run package

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    appBundle=""
    for appDir in desktop/out/Goose-darwin-*; do
      candidate="$appDir/${appBundleName}"
      if [ -d "$candidate" ]; then
        appBundle="$candidate"
        break
      fi
    done

    if [ -z "$appBundle" ]; then
      echo "failed to locate packaged ${appBundleName} in desktop/out" >&2
      exit 1
    fi

    mkdir -p "$out/Applications" "$out/bin"
    cp -R "$appBundle" "$out/Applications/${appBundleName}"

    ${lib.getExe python3} - "$out/Applications/${appBundleName}/Contents/Info.plist" <<'PY'
    import plistlib
    import sys

    path = sys.argv[1]
    with open(path, "rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleDisplayName"] = "${appName}"
    info["CFBundleIdentifier"] = "${appId}"
    info["CFBundleName"] = "${appName}"
    info["CFBundleShortVersionString"] = "${version}"
    info["CFBundleVersion"] = "${version}"
    url_types = info.setdefault("CFBundleURLTypes", [])
    url_entry = {
        "CFBundleURLName": "${appName}",
        "CFBundleURLSchemes": ["${appProtocolScheme}"],
    }
    if url_entry not in url_types:
        url_types.append(url_entry)
    with open(path, "wb") as handle:
        plistlib.dump(info, handle)
    PY

    makeWrapper \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appExecutableName}" \
      "$out/bin/${pname}"

    runHook postInstall
  '';

  postFixup = ''
    /usr/bin/xattr -cr "$out/Applications/${appBundleName}"
    /usr/bin/codesign --force --deep --sign - "$out/Applications/${appBundleName}"
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    for path in \
      "$out/Applications/${appBundleName}" \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appExecutableName}" \
      "$out/Applications/${appBundleName}/Contents/Resources/bin/goosed" \
      "$out/bin/${pname}"
    do
      if [ ! -e "$path" ]; then
        echo "missing required runtime path: $path" >&2
        exit 1
      fi
    done

    /usr/bin/codesign --verify --deep --strict "$out/Applications/${appBundleName}"
    ${lib.getExe python3} - "$out/Applications/${appBundleName}/Contents/Info.plist" <<'PY'
    import plistlib
    import sys

    path = sys.argv[1]
    with open(path, "rb") as handle:
        info = plistlib.load(handle)
    expected = {
        "CFBundleDisplayName": "${appName}",
        "CFBundleIdentifier": "${appId}",
        "CFBundleName": "${appName}",
        "CFBundleShortVersionString": "${version}",
        "CFBundleVersion": "${version}",
    }
    for key, value in expected.items():
        actual = info.get(key)
        if actual != value:
            raise SystemExit(f"unexpected {key}: {actual!r}")
    schemes = [
        scheme
        for entry in info.get("CFBundleURLTypes", [])
        for scheme in entry.get("CFBundleURLSchemes", [])
    ]
    if "${appProtocolScheme}" not in schemes:
        raise SystemExit("missing goose URL scheme")
    PY

    runHook postInstallCheck
  '';

  passthru = {
    inherit
      appId
      appName
      appProtocolScheme
      electronDist
      electronRuntime
      electronRuntimeVersion
      electronVersion
      pnpmDeps
      ;

    macApp = {
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
  };

  meta = with lib; {
    description = "Goose desktop app";
    homepage = "https://github.com/aaif-goose/goose";
    license = licenses.asl20;
    mainProgram = pname;
    platforms = [ "aarch64-darwin" ];
  };
}
