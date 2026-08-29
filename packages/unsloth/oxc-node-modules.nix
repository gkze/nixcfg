{
  buildNpmPackage,
  fetchNpmDeps,
  lib,
  nodejs,
  npmDepsHash,
  src,
  stdenv,
  version,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
buildNpmPackage {
  pname = "unsloth-oxc-node-modules";
  inherit
    nodejs
    src
    version
    ;
  npmDeps = fetchNpmDeps {
    name = "unsloth-oxc-node-modules-${version}-npm-deps";
    inherit src;
    sourceRoot = "${src.name}/studio/backend/core/data_recipe/oxc-validator";
    hash = npmDepsHash;
  };

  npmRoot = "studio/backend/core/data_recipe/oxc-validator";
  npmInstallFlags = [
    "--ignore-scripts"
    "--no-audit"
    "--no-fund"
  ];
  npmRebuildFlags = [ "--ignore-scripts" ];
  dontNpmBuild = true;
  strictDeps = true;

  buildPhase = ''
    runHook preBuild
    cd "$npmRoot"

    npm ls --all --offline
    /usr/bin/lipo \
      node_modules/@oxc-parser/binding-darwin-arm64/parser.darwin-arm64.node \
      -verify_arch arm64
    /usr/bin/lipo \
      node_modules/@oxlint/binding-darwin-arm64/oxlint.darwin-arm64.node \
      -verify_arch arm64
    node --input-type=module - <<'JS'
    import { parseSync } from "oxc-parser";
    const valid = parseSync("input.js", "const value = 1;");
    const invalid = parseSync("input.js", "const = ;");
    if (valid.errors.length !== 0 || invalid.errors.length === 0) process.exit(1);
    JS
    node node_modules/oxlint/bin/oxlint --version

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p "$out"
    cp -R node_modules "$out/node_modules"
    cp package.json package-lock.json validate.mjs "$out/"

    runHook postInstall
  '';

  meta = {
    description = "Package-owned OXC runtime for Unsloth Studio";
    license = lib.licenses.mit;
    platforms = [ "aarch64-darwin" ];
  };
}
