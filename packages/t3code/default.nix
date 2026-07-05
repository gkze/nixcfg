{
  bun,
  cacert,
  fetchPnpmDeps ? null,
  inputs,
  lib,
  makeWrapper,
  nodejs,
  outputs,
  pnpm_10,
  pnpmConfigHook,
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
      fetchPnpmDeps
      inputs
      lib
      nodejs
      outputs
      pnpm_10
      pnpmConfigHook
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
  pythonForRuntimeManifest = python3.withPackages (ps: [ ps.pyyaml ]);

  node_modules = stdenv.mkDerivation {
    pname = "${pname}-node_modules";
    inherit version src;

    nativeBuildInputs = [
      bun
      cacert
      pythonForRuntimeManifest
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

      ${lib.getExe pythonForRuntimeManifest} ${../t3code-desktop/render_runtime_package_json.py} \
        ${src} \
        --output package.json \
        --server-only
      cp ${./bun.lock} bun.lock

      bun install \
        --frozen-lockfile \
        --ignore-scripts \
        --no-progress

      ${lib.getExe pythonForRuntimeManifest} - <<'PY'
      import json
      from pathlib import Path

      package_json = Path("node_modules/@pierre/diffs/package.json")
      payload = json.loads(package_json.read_text(encoding="utf-8"))
      exports = payload.get("exports")
      if not isinstance(exports, dict):
          raise TypeError("@pierre/diffs package.json exports must be an object")
      exports["./utils/*"] = {
          "types": "./dist/utils/*.d.ts",
          "import": "./dist/utils/*.js",
      }
      package_json.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
      PY

      # Bun can create package-local bin links nondeterministically inside its
      # private .bun store. The runtime only needs the top-level node_modules
      # links, so remove nested .bin directories before hashing the output.
      if [ -d node_modules/.bun ]; then
        find node_modules/.bun -path '*/node_modules/.bin' -type d -prune -exec rm -rf {} +
      fi

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
