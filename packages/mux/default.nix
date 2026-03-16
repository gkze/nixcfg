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
  lib,
  ...
}:
let
  pname = "mux";
  version = outputs.lib.getFlakeVersion pname;
  src = inputs.mux;
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
    outputHash = outputs.lib.sourceHash pname "nodeModulesHash";
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
    mac = data.setdefault("build", {}).setdefault("mac", {})
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
    export HOME="$TMPDIR"
    cp -r ${offlineCache}/node_modules .
    chmod -R u+w node_modules

    patchShebangs node_modules
    patchShebangs scripts

    ./scripts/postinstall.sh
    touch node_modules/.installed
  '';

  buildPhase =
    if stdenv.hostPlatform.isDarwin then
      ''
        runHook preBuild

        export HOME="$TMPDIR"
        export LD_LIBRARY_PATH="${lib.makeLibraryPath [ stdenv.cc.cc.lib ]}:$LD_LIBRARY_PATH"
        export CSC_IDENTITY_AUTO_DISCOVERY=false

        make SHELL=${stdenv.shell} build
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
