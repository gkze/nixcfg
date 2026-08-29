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

    manifest="$TMPDIR/unsloth-frontend.manifest"
    : > "$manifest"
    while IFS= read -r -d "" file; do
      relative="''${file#"$out/dist/"}"
      digest="$(sha256sum "$file" | cut -d ' ' -f 1)"
      printf '%s  %s\n' "$digest" "$relative" >> "$manifest"
    done < <(find "$out/dist" -type f -print0 | LC_ALL=C sort -z)

    test "$(wc -l < "$manifest" | tr -d ' ')" = 704
    test "$(sha256sum "$manifest" | cut -d ' ' -f 1)" = \
      03acd2b8ef28d7135bd74a5b7ed82e6eaecea5289cfa4883ece0ef34597b6125

    runHook postInstallCheck
  '';

  meta = {
    description = "Lifecycle-suppressed Unsloth Studio frontend";
    license = lib.licenses.agpl3Only;
    platforms = [ "aarch64-darwin" ];
  };
}
