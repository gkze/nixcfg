{
  bun,
  cacert,
  inputs,
  lib,
  makeWrapper,
  nodejs,
  outputs,
  python3,
  stdenv,
  stdenvNoCC,
  ...
}:
let
  shared = import ./_shared.nix {
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
    pname
    src
    version
    workspaceBuild
    ;
  inherit (stdenv.hostPlatform) system;

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

      ${lib.getExe python3} ${../t3code-desktop/render_runtime_package_json.py} \
        ${src} \
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
      cp -R node_modules "$out/node_modules"
      cp package.json "$out/package.json"
      cp bun.lock "$out/bun.lock"

      runHook postInstall
    '';

    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = outputs.lib.sourceHashForPlatform pname "nodeModulesHash" system;
  };
in
stdenvNoCC.mkDerivation {
  inherit
    pname
    version
    node_modules
    ;

  dontUnpack = true;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/libexec/${pname}" "$out/bin"
    cp -R ${workspaceBuild}/apps/server/dist "$out/libexec/${pname}/dist"
    cp -R ${node_modules}/node_modules "$out/libexec/${pname}/node_modules"

    makeWrapper ${lib.getExe bun} "$out/bin/t3" \
      --add-flags "$out/libexec/${pname}/dist/bin.mjs"

    runHook postInstall
  '';

  passthru = {
    inherit node_modules workspaceBuild;
  };

  meta = with lib; {
    description = "T3 Code browser/server runtime";
    homepage = "https://github.com/pingdotgg/t3code";
    license = licenses.mit;
    mainProgram = "t3";
    platforms = [ "aarch64-darwin" ];
  };
}
