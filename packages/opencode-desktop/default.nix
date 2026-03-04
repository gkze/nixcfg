{
  inputs,
  outputs,
  rustPlatform,
  opencode,
  stdenv,
  lib,
  ...
}:
let
  slib = outputs.lib;
  spectaEntry = slib.sourceHashEntry "opencode-desktop" "spectaOutputHash";
  tauriEntry = slib.sourceHashEntry "opencode-desktop" "tauriOutputHash";
  tauriSpectaEntry = slib.sourceHashEntry "opencode-desktop" "tauriSpectaOutputHash";
  upstreamDesktop = inputs.opencode.packages.${stdenv.hostPlatform.system}.desktop;
in
rustPlatform.buildRustPackage {
  inherit (upstreamDesktop)
    pname
    version
    src
    patches
    cargoRoot
    buildAndTestSubdir
    nativeBuildInputs
    buildInputs
    strictDeps
    meta
    ;

  inherit (opencode) node_modules;

  cargoLock = {
    lockFile = "${upstreamDesktop.src}/packages/desktop/src-tauri/Cargo.lock";
    outputHashes = {
      ${spectaEntry.gitDep} = spectaEntry.hash;
      ${tauriEntry.gitDep} = tauriEntry.hash;
      ${tauriSpectaEntry.gitDep} = tauriSpectaEntry.hash;
    };
  };

  tauriBuildFlags = [ "--no-sign" ];

  preBuild = ''
    cp -a ${opencode.node_modules}/{node_modules,packages} .
    chmod -R u+w node_modules packages
    patchShebangs node_modules
    patchShebangs packages/desktop/node_modules

    mkdir -p packages/desktop/src-tauri/sidecars
    cp ${opencode}/bin/opencode packages/desktop/src-tauri/sidecars/opencode-cli-${stdenv.hostPlatform.rust.rustcTarget}
  '';

  postFixup = lib.optionalString stdenv.hostPlatform.isLinux ''
    mv $out/bin/OpenCode $out/bin/opencode-desktop
    sed -i 's|^Exec=OpenCode$|Exec=opencode-desktop|' $out/share/applications/OpenCode.desktop
  '';
}
