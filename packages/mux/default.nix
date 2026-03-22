{
  inputs,
  outputs,
  stdenv,
  stdenvNoCC,
  bun,
  nodejs,
  makeWrapper,
  gnumake,
  git,
  python3,
  electron,
  cacert,
  fetchurl,
  lib,
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
  hashToNix32 =
    hash:
    builtins.convertHash {
      inherit hash;
      hashAlgo = "sha256";
      toHashFormat = "nix32";
    };
  electronHeadersChecksum =
    {
      # From node-v38.7.2-headers.tar.gz
      "38.7.2" = "9c571a305727c9d8188971c36df3f953268e1b9f4135d322fe75609ad3ca5318";
    }
    .${electronVersion}
      or (throw "packages/mux/default.nix missing Electron headers hash for ${electronVersion}");
  electronHeaders = stdenvNoCC.mkDerivation {
    name = "electron-headers-${electronVersion}";
    src = fetchurl {
      url = "https://www.electronjs.org/headers/v${electronVersion}/node-v${electronVersion}-headers.tar.gz";
      sha256 = hashToNix32 electronHeadersChecksum;
    };
    dontUnpack = true;
    dontBuild = true;
    dontFixup = true;
    installPhase = ''
      mkdir -p "$out"
      tar -xzf "$src" --strip-components=1 -C "$out"
    '';
  };
  electronZipPlatform =
    if stdenv.hostPlatform.system == "aarch64-darwin" then
      "darwin-arm64"
    else if stdenv.hostPlatform.system == "x86_64-darwin" then
      "darwin-x64"
    else
      throw "packages/mux/default.nix unsupported Darwin platform ${stdenv.hostPlatform.system}";
  electronZipChecksum =
    {
      "38.7.2" = {
        # From node_modules/electron/checksums.json
        "darwin-arm64" = "b91e12ec6695f969ccf792d95dc7ea5da35f399cec2bed4d7b25d8a1f545b5de";
        "darwin-x64" = "459dd05f00c29d435112596f87bc5bd0aeb16796dff0744e5421086417877d24";
      };
    }
    .${electronVersion}.${electronZipPlatform}
      or (throw "packages/mux/default.nix missing Electron zip hash for ${electronVersion}/${electronZipPlatform}");
  electronZip = fetchurl {
    url = "https://github.com/electron/electron/releases/download/v${electronVersion}/electron-v${electronVersion}-${electronZipPlatform}.zip";
    sha256 = hashToNix32 electronZipChecksum;
  };
  electronDist = stdenvNoCC.mkDerivation {
    name = "electron-dist-${electronVersion}-${electronZipPlatform}";
    dontUnpack = true;
    dontBuild = true;
    dontFixup = true;
    installPhase = ''
      mkdir -p "$out"
      ln -s ${electronZip} "$out/electron-v${electronVersion}-${electronZipPlatform}.zip"
    '';
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
    python3 - <<'PY'
    import json
    from pathlib import Path

    path = Path("package.json")
    data = json.loads(path.read_text())
    build = data.setdefault("build", {})
    build["electronDist"] = "${electronDist}"

    mac = build.setdefault("mac", {})
    mac["target"] = "dir"
    mac["hardenedRuntime"] = False
    mac["notarize"] = False
    path.write_text(json.dumps(data, indent=2) + "\n")
    PY
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
    electron
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

        # Generate app icons once up front to avoid parallel make races inside
        # scripts/generate-icons.ts writing build/icon.iconset.
        bun scripts/generate-icons.ts png icns linux-icons

        make SHELL=${stdenv.shell} build-main build-preload build-renderer build-static
        bun x electron-builder --mac --dir --publish never -c.mac.identity=null

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

        makeWrapper ${electron}/bin/electron "$out/bin/mux" \
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
        mkdir -p "$out/share/applications"
        cat > "$out/share/applications/mux.desktop" <<'EOF'
        [Desktop Entry]
        Name=Mux
        GenericName=Agent Multiplexer
        Comment=Agent Multiplexer
        Exec=$out/bin/mux %U
        Icon=mux
        Terminal=false
        Type=Application
        Categories=Development;
        StartupWMClass=mux
        EOF

        runHook postInstall
      '';

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
