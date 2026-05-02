{
  autoconf,
  automake,
  cctools,
  coreutils,
  dpkg,
  fetchPnpmDeps ? null,
  git,
  inputs,
  lib,
  libiconv,
  libsecret,
  libutempter,
  nodejs_24,
  openssl,
  outputs,
  patchelf,
  pkg-config,
  pnpmConfigHook,
  pnpm_10,
  nixcfgElectron,
  python3,
  rpm,
  sqlite,
  stdenv,
  zlib,
  ...
}:
let
  pname = "emdash";
  slib = outputs.lib;
  version = slib.getFlakeVersion pname;
  src = inputs.emdash;
  nodejs = nodejs_24;
  pnpm = pnpm_10.override { inherit nodejs; };
  inherit (stdenv.hostPlatform) system;
  npmDepsHash =
    let
      perPlatformHash = builtins.tryEval (slib.sourceHashForPlatform pname "npmDepsHash" system);
    in
    if perPlatformHash.success then perPlatformHash.value else slib.sourceHash pname "npmDepsHash";

  electronVersion = "40.7.0";
  electronRuntime = nixcfgElectron.runtimeFor electronVersion;
  electronRuntimeVersion = electronRuntime.version;
  electronHeaders = electronRuntime.passthru.headers;
  electronDist = electronRuntime.passthru.dist;
  supportedSystems = [
    "aarch64-darwin"
    "aarch64-linux"
    "x86_64-linux"
  ];
  electronBuilderTarget = if stdenv.hostPlatform.isDarwin then "mac" else "linux";
  patchNodeAddonApi = ./patch_node_addon_api.py;
  msShim = ./ms-shim.cjs;

  pnpmDeps =
    if fetchPnpmDeps != null then
      fetchPnpmDeps {
        inherit
          pname
          version
          src
          pnpm
          ;
        fetcherVersion = 3;
        hash = npmDepsHash;
      }
    else
      pnpm.fetchDeps {
        inherit
          pname
          version
          src
          ;
        fetcherVersion = 3;
        hash = npmDepsHash;
      };
in
stdenv.mkDerivation {
  inherit
    pname
    version
    src
    pnpmDeps
    ;

  nativeBuildInputs = [
    autoconf
    automake
    coreutils
    git
    nodejs
    pkg-config
    pnpm
    pnpmConfigHook
    python3
  ]
  ++ lib.optionals stdenv.hostPlatform.isDarwin [
    cctools
    libiconv
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [
    dpkg
    patchelf
    rpm
  ];

  buildInputs = [
    openssl
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [
    libsecret
    libutempter
    sqlite
    zlib
  ];

  strictDeps = true;

  env = {
    CI = "1";
    EMDASH_NIXCFG_BUILD_REV = "1";
    ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
    npm_config_build_from_source = "true";
    npm_config_manage_package_manager_versions = "false";
    npm_config_node_linker = "hoisted";
  };

  postPatch = ''
    substituteInPlace src/main/utils/userEnv.ts \
      --replace-fail " -ilc 'env'" " -lc 'env'"
  '';

  buildPhase = ''
    runHook preBuild

    export HOME="$TMPDIR/emdash-home"
    mkdir -p "$HOME"
    pnpm config set manage-package-manager-versions false

    export npm_config_runtime=electron
    export npm_config_target=${electronVersion}
    export npm_config_nodedir=${lib.escapeShellArg (toString electronHeaders)}

    # Work around keytar's bundled node-addon-api constant-expression issue
    # with the Apple toolchain in this build environment.
    ${lib.getExe python3} ${patchNodeAddonApi}

    # pnpmConfigHook installs dependencies without relying on upstream
    # postinstall scripts, so rebuild native Electron modules explicitly.
    pnpm exec electron-rebuild -f -v ${electronVersion} --only=better-sqlite3,node-pty

    pnpm run build

    install -Dm644 ${msShim} out/main/ms-shim.cjs

    substituteInPlace node_modules/debug/src/common.js \
      --replace-fail "require('ms')" \
        "require('../../../out/main/ms-shim.cjs')"

    electronDistDir="$PWD/electron-dist"
    mkdir -p "$electronDistDir"
    cp -R ${electronDist}/. "$electronDistDir"/
    chmod -R u+w "$electronDistDir"

    extra_electron_builder_flags=()
    ${lib.optionalString stdenv.hostPlatform.isDarwin ''
      extra_electron_builder_flags+=(-c.mac.identity=null)
    ''}

    pnpm exec electron-builder \
      --${electronBuilderTarget} \
      --dir \
      --publish never \
      -c.electronDist="$electronDistDir" \
      -c.electronVersion=${lib.escapeShellArg electronRuntimeVersion} \
      -c.npmRebuild=false \
      "''${extra_electron_builder_flags[@]}"

    runHook postBuild
  '';

  installPhase =
    if stdenv.hostPlatform.isDarwin then
      ''
        runHook preInstall

        distDir="$PWD/dist"
        appDir="$distDir/mac-arm64/Emdash.app"

        if [ ! -d "$appDir" ]; then
          echo \
            "Expected Emdash.app output from electron-builder, got nothing at $appDir" \
            >&2
          exit 1
        fi

        install -d "$out/Applications"
        cp -R "$appDir" "$out/Applications/"

        install -d "$out/bin"
        install -m755 ${./launcher-darwin.sh} "$out/bin/emdash"
        substituteInPlace "$out/bin/emdash" \
          --replace-fail "#!/usr/bin/env bash" "#!${stdenv.shell}" \
          --replace-fail "@out@" "$out"

        runHook postInstall
      ''
    else
      ''
        runHook preInstall

        distDir="$PWD/dist"
        shopt -s nullglob
        unpackedDirs=("$distDir"/linux*-unpacked)
        if [ "''${#unpackedDirs[@]}" -ne 1 ]; then
          printf \
            'Expected exactly one linux*-unpacked output from electron-builder, found %s\n' \
            "''${#unpackedDirs[@]}" \
            >&2
          exit 1
        fi
        unpackedDir="''${unpackedDirs[0]}"

        install -d "$out/share/emdash"
        cp -R "$unpackedDir" "$out/share/emdash/linux-unpacked"

        install -d "$out/bin"
        install -m755 ${./launcher-linux.sh} "$out/bin/emdash"
        substituteInPlace "$out/bin/emdash" \
          --replace-fail "#!/usr/bin/env bash" "#!${stdenv.shell}" \
          --replace-fail "@out@" "$out"

        runHook postInstall
      '';

  passthru = {
    inherit
      electronDist
      electronHeaders
      electronRuntime
      electronRuntimeVersion
      electronVersion
      ;
  };

  meta = with lib; {
    description = "Agentic development environment for parallel coding agents";
    homepage = "https://github.com/generalaction/emdash";
    license = licenses.mit;
    platforms = supportedSystems;
    mainProgram = pname;
  };
}
