{
  inputs,
  slib,
  system,
  final,
  prev,
  ...
}:
{
  opencode-desktop =
    let
      # Get Cargo.lock git dependency hashes from sources.json
      spectaEntry = slib.sourceHashEntry "opencode-desktop" "spectaOutputHash";
      tauriEntry = slib.sourceHashEntry "opencode-desktop" "tauriOutputHash";
      tauriSpectaEntry = slib.sourceHashEntry "opencode-desktop" "tauriSpectaOutputHash";
      upstreamDesktop = inputs.opencode.packages.${system}.desktop;
    in
    prev.rustPlatform.buildRustPackage {
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

      # Reuse the CLI's node_modules which has the correct hash override
      # from sources.json, rather than inheriting upstream's potentially
      # stale fixed-output hash.
      inherit (final.opencode) node_modules;

      # Add output hashes for Cargo.lock git dependencies
      cargoLock = {
        lockFile = "${upstreamDesktop.src}/packages/desktop/src-tauri/Cargo.lock";
        outputHashes = {
          ${spectaEntry.gitDep} = spectaEntry.hash;
          ${tauriEntry.gitDep} = tauriEntry.hash;
          ${tauriSpectaEntry.gitDep} = tauriSpectaEntry.hash;
        };
      };

      # Use dev config instead of prod (gets dev icon + "OpenCode Dev" name)
      tauriBuildFlags = [ "--no-sign" ];

      # Override preBuild to use our patched opencode instead of upstream's
      preBuild = ''
        cp -a ${final.opencode.node_modules}/{node_modules,packages} .
        chmod -R u+w node_modules packages
        patchShebangs node_modules
        patchShebangs packages/desktop/node_modules

        mkdir -p packages/desktop/src-tauri/sidecars
        cp ${final.opencode}/bin/opencode packages/desktop/src-tauri/sidecars/opencode-cli-${prev.stdenv.hostPlatform.rust.rustcTarget}
      '';

      postFixup = prev.lib.optionalString prev.stdenv.hostPlatform.isLinux ''
        mv $out/bin/OpenCode $out/bin/opencode-desktop
        sed -i 's|^Exec=OpenCode$|Exec=opencode-desktop|' $out/share/applications/OpenCode.desktop
      '';
    };
}
