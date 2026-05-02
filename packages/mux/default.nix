{
  inputs,
  outputs,
  stdenv,
  stdenvNoCC,
  bun,
  nodejs,
  makeWrapper,
  makeDesktopItem,
  gnumake,
  git,
  python3,
  cacert,
  lib,
  nixcfgElectron,
  ...
}:
let
  pname = "mux";
  version = outputs.lib.getFlakeVersion pname;
  src = inputs.mux;
  # Keep this in sync with the exact Electron version resolved in mux's bun.lock.
  # We validate it against node_modules/electron/package.json during configurePhase
  # so lockfile bumps fail loudly until the matching headers hash is updated.
  electronVersion = "38.7.2";
  electronRuntime = nixcfgElectron.runtimeFor electronVersion;
  electronRuntimeVersion = electronRuntime.version;
  electronHeaders = electronRuntime.passthru.headers;
  electronDist = electronRuntime.passthru.dist;
  linuxDesktopItem = makeDesktopItem {
    name = pname;
    desktopName = "Mux";
    genericName = "Agent Multiplexer";
    comment = "Agent Multiplexer";
    exec = "${pname} %U";
    icon = pname;
    categories = [ "Development" ];
    startupWMClass = pname;
  };
  offlineCache = stdenvNoCC.mkDerivation {
    name = "${pname}-deps-${version}";

    src = stdenvNoCC.mkDerivation {
      name = "${pname}-lock-files";
      inherit src;
      dontUnpack = true;
      installPhase = ''
        mkdir -p "$out"
        cp "$src/package.json" "$out/package.json"
        cp "$src/bun.lock" "$out/bun.lock"
      '';
    };

    nativeBuildInputs = [
      bun
      cacert
    ];

    dontPatchShebangs = true;
    dontFixup = true;

    buildPhase = ''
      export HOME="$TMPDIR"
      export BUN_INSTALL_CACHE_DIR="$TMPDIR/.bun-cache"
      bun install --frozen-lockfile --no-progress --ignore-scripts
    '';

    installPhase = ''
      mkdir -p "$out"
      cp -r node_modules "$out/"
    '';

    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = outputs.lib.sourceHashForPlatform pname "nodeModulesHash" stdenv.hostPlatform.system;
  };
in
stdenv.mkDerivation {
  inherit
    pname
    version
    src
    offlineCache
    ;

  postUnpack = ''
    chmod -R u+w source
  '';

  postPatch = lib.optionalString stdenv.hostPlatform.isDarwin ''
    ${lib.getExe python3} \
      ${./patch_package_json.py} \
      package.json \
      electron-dist
  '';

  nativeBuildInputs = [
    bun
    nodejs
    makeWrapper
    gnumake
    git
    python3
  ];

  buildInputs = [
    electronRuntime
    stdenv.cc.cc.lib
  ];

  configurePhase = ''
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    export SSL_CERT_FILE="${cacert}/etc/ssl/certs/ca-bundle.crt"
    export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE"
    export npm_config_nodedir="${electronHeaders}"

    cp -r ${offlineCache}/node_modules .
    chmod -R u+w node_modules

    patchShebangs node_modules
    patchShebangs scripts

    resolvedElectronVersion="$(node -p "require('./node_modules/electron/package.json').version")"
    if [ "$resolvedElectronVersion" != "${electronVersion}" ]; then
      echo "mux electron version mismatch: expected ${electronVersion}, got $resolvedElectronVersion" >&2
      echo "Update packages/mux/default.nix with the new Electron headers hash." >&2
      exit 1
    fi

    ./scripts/postinstall.sh
    touch node_modules/.installed
  '';

  buildPhase =
    if stdenv.hostPlatform.isDarwin then
      ''
        runHook preBuild

        export HOME="$TMPDIR/home"
        mkdir -p "$HOME"
        export LD_LIBRARY_PATH="${lib.makeLibraryPath [ stdenv.cc.cc.lib ]}:$LD_LIBRARY_PATH"
        export CSC_IDENTITY_AUTO_DISCOVERY=false
        export NODE_OPTIONS="--max-old-space-size=6144''${NODE_OPTIONS:+ $NODE_OPTIONS}"

        electronDistDir="$PWD/electron-dist"
        mkdir -p "$electronDistDir"
        cp -R ${electronDist}/. "$electronDistDir"/
        chmod -R u+w "$electronDistDir"

        # Generate app icons once up front to avoid parallel make races inside
        # scripts/generate-icons.ts writing build/icon.iconset.
        bun scripts/generate-icons.ts png icns linux-icons

        make SHELL=${stdenv.shell} build-main build-preload build-renderer build-static
        bun x electron-builder \
          --mac \
          --dir \
          --publish never \
          -c.electronDist="$electronDistDir" \
          -c.electronVersion=${lib.escapeShellArg electronRuntimeVersion} \
          -c.mac.identity=null

        runHook postBuild
      ''
    else
      ''
        runHook preBuild

        export HOME="$TMPDIR"
        export LD_LIBRARY_PATH="${lib.makeLibraryPath [ stdenv.cc.cc.lib ]}:$LD_LIBRARY_PATH"

        make SHELL=${stdenv.shell} build

        runHook postBuild
      '';

  installPhase =
    if stdenv.hostPlatform.isDarwin then
      ''
        runHook preInstall

        appBundle="$(printf '%s\n' release/mac*/mux.app | head -n 1)"
        if [ ! -d "$appBundle" ]; then
          echo "failed to locate packaged mux.app in release" >&2
          exit 1
        fi

        mkdir -p "$out/Applications"
        cp -R "$appBundle" "$out/Applications/mux.app"

        mkdir -p "$out/bin"
        ln -s "$out/Applications/mux.app/Contents/MacOS/mux" "$out/bin/mux"

        runHook postInstall
      ''
    else
      ''
        runHook preInstall

        mkdir -p "$out/lib/mux"
        mkdir -p "$out/bin"

        cp -r dist "$out/lib/mux/"
        cp -r node_modules "$out/lib/mux/"
        cp package.json "$out/lib/mux/"

        makeWrapper ${electronRuntime}/bin/electron "$out/bin/mux" \
          --add-flags "$out/lib/mux/dist/cli/index.js" \
          --set MUX_E2E_LOAD_DIST "1" \
          --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath [ stdenv.cc.cc.lib ]}" \
          --prefix PATH : "${
            lib.makeBinPath [
              git
              stdenv.shell
            ]
          }"

        install -Dm644 public/icon.png "$out/share/icons/hicolor/512x512/apps/mux.png"
        install -Dm644 \
          ${linuxDesktopItem}/share/applications/${pname}.desktop \
          "$out/share/applications/${pname}.desktop"

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

  doInstallCheck = stdenv.hostPlatform.isDarwin;
  installCheckPhase = lib.optionalString stdenv.hostPlatform.isDarwin ''
    runHook preInstallCheck

    requiredRuntimePaths="
      $out/Applications/mux.app
      $out/Applications/mux.app/Contents/MacOS/mux
      $out/bin/mux
    "

    for path in $requiredRuntimePaths; do
      if [ ! -e "$path" ]; then
        echo "missing required runtime path: $path" >&2
        exit 1
      fi
    done

    runHook postInstallCheck
  '';

  meta = with lib; {
    description = "mux - coder multiplexer";
    homepage = "https://github.com/coder/mux";
    license = licenses.agpl3Only;
    platforms = platforms.linux ++ platforms.darwin;
    mainProgram = "mux";
  };
}
