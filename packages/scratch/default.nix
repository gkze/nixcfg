{
  rustPlatform,
  cargo-tauri,
  npmHooks,
  fetchNpmDeps,
  nodejs,
  pkg-config,
  makeWrapper,
  inputs,
  outputs,
  lib,
  stdenv,
  ...
}:
let
  name = "scratch";
  slib = outputs.lib;
  version = slib.getFlakeVersion name;
  tauriDir = "src-tauri";
in
rustPlatform.buildRustPackage {
  pname = name;
  inherit version;
  src = inputs.scratch;

  npmDeps = fetchNpmDeps {
    name = "${name}-${version}-npm-deps";
    src = inputs.scratch;
    hash = slib.sourceHash name "npmDepsHash";
  };

  cargoHash = slib.sourceHash name "cargoHash";

  cargoRoot = tauriDir;
  buildAndTestSubdir = tauriDir;

  nativeBuildInputs = [
    cargo-tauri.hook
    npmHooks.npmConfigHook
    nodejs
    pkg-config
    makeWrapper
  ];

  doCheck = false;

  postInstall = lib.optionalString stdenv.hostPlatform.isDarwin ''
    mkdir -p $out/bin
    makeWrapper $out/Applications/Scratch.app/Contents/MacOS/Scratch $out/bin/${name}
  '';

  meta = {
    description = "Minimalist, offline-first markdown note-taking app";
    homepage = "https://github.com/erictli/scratch";
    license = lib.licenses.mit;
    mainProgram = name;
    platforms = lib.platforms.darwin;
  };
}
