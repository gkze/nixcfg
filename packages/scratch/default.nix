{
  rustPlatform,
  cargo-tauri,
  npmHooks,
  fetchNpmDeps,
  nodejs,
  pkg-config,
  makeWrapper,
  wrapGAppsHook4,
  webkitgtk_4_1,
  librsvg,
  openssl,
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
  rustTarget = stdenv.hostPlatform.rust.rustcTarget;
  rustTargetEnv = lib.toUpper (lib.replaceStrings [ "-" ] [ "_" ] rustTarget);
  baseTauriHook = cargo-tauri.hook;
  tauriHook =
    if stdenv.hostPlatform.isLinux then
      baseTauriHook.overrideAttrs (_: {
        name = "scratch-tauri-hook-${rustTarget}";

        buildCommand = ''
          mkdir -p "$out/nix-support"
          cp ${baseTauriHook}/nix-support/setup-hook "$out/nix-support/setup-hook"

          substituteInPlace "$out/nix-support/setup-hook" \
            --replace-fail '"x86_64-unknown-linux-gnu"' '"${rustTarget}"' \
            --replace-fail 'target/x86_64-unknown-linux-gnu' 'target/${rustTarget}' \
            --replace-fail 'CC_X86_64_UNKNOWN_LINUX_GNU' 'CC_${rustTargetEnv}' \
            --replace-fail 'CXX_X86_64_UNKNOWN_LINUX_GNU' 'CXX_${rustTargetEnv}' \
            --replace-fail 'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER' 'CARGO_TARGET_${rustTargetEnv}_LINKER'
        '';
      })
    else
      cargo-tauri.hook;
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
  tauriBuildFlags = lib.optionals stdenv.hostPlatform.isDarwin [ "--no-sign" ];

  nativeBuildInputs = [
    tauriHook
    npmHooks.npmConfigHook
    nodejs
    pkg-config
    makeWrapper
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [
    wrapGAppsHook4
    cargo-tauri
  ];

  buildInputs = lib.optionals stdenv.hostPlatform.isLinux [
    openssl
    webkitgtk_4_1
    librsvg
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
    platforms = lib.platforms.darwin ++ lib.platforms.linux;
  };
}
