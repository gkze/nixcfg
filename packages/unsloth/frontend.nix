{
  buildNpmPackage,
  fetchNpmDeps,
  lib,
  nodejs,
  npmDepsHash,
  python3,
  src,
  stdenv,
  version,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
buildNpmPackage {
  pname = "unsloth-frontend";
  inherit
    nodejs
    src
    version
    ;

  npmDeps = fetchNpmDeps {
    name = "unsloth-frontend-${version}-npm-deps";
    inherit src;
    sourceRoot = "${src.name}/studio/frontend";
    hash = npmDepsHash;
  };

  npmRoot = "studio/frontend";
  npmInstallFlags = [
    "--ignore-scripts"
    "--no-audit"
    "--no-fund"
  ];
  npmRebuildFlags = [ "--ignore-scripts" ];
  dontNpmBuild = true;
  strictDeps = true;

  nativeBuildInputs = [ python3 ];
  postPatch = ''
    PYTHONPATH=${
      lib.fileset.toSource {
        root = ../..;
        fileset = lib.fileset.unions [
          ../../lib/__init__.py
          ../../lib/exact_text_patch.py
        ];
      }
    } ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD"
  '';

  buildPhase = ''
    runHook preBuild
    cd "$npmRoot"

    test -x node_modules/@biomejs/cli-darwin-arm64/biome
    /usr/bin/lipo \
      node_modules/@biomejs/cli-darwin-arm64/biome \
      -verify_arch arm64
    test -f node_modules/vite/node_modules/fsevents/fsevents.node
    /usr/bin/lipo \
      node_modules/vite/node_modules/fsevents/fsevents.node \
      -verify_arch arm64
    test ! -e public/mockServiceWorker.js
    npm ls --all --offline
    npm run build

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    test -f dist/index.html
    mkdir -p "$out"
    cp -R dist "$out/dist"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    test -s "$out/dist/index.html"

    runHook postInstallCheck
  '';

  meta = {
    description = "Lifecycle-suppressed Unsloth Studio frontend";
    license = lib.licenses.agpl3Only;
    platforms = [ "aarch64-darwin" ];
  };
}
