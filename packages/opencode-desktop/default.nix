{
  pkgs,
  inputs,
  outputs,
  opencode,
  stdenv,
  lib,
  runCommand,
  ...
}:
let
  inherit (builtins)
    fromJSON
    fromTOML
    readFile
    ;

  slib = outputs.lib;
  upstreamDesktop = inputs.opencode.packages.${stdenv.hostPlatform.system}.desktop;
  inherit (upstreamDesktop) pname version;

  cargoManifestVersion =
    (fromTOML (readFile "${inputs.opencode}/packages/desktop/src-tauri/Cargo.toml")).package.version;
  desktopPackageVersion =
    (fromJSON (readFile "${inputs.opencode}/packages/desktop/package.json")).version;

  patchedSrc = runCommand "${pname}-${version}-src" { } ''
    cp -r ${upstreamDesktop.src} "$out"
    chmod -R u+w "$out"

    if [ ! -f "$out/.github/TEAM_MEMBERS" ]; then
      mkdir -p "$out/.github"
      if [ -f ${inputs.opencode}/.github/TEAM_MEMBERS ]; then
        cp ${inputs.opencode}/.github/TEAM_MEMBERS "$out/.github/TEAM_MEMBERS"
      else
        touch "$out/.github/TEAM_MEMBERS"
      fi
    fi
  '';

  cargoNix = import ./Cargo.nix {
    inherit pkgs;
    rootSrc = patchedSrc;
  };
  cargoNixVersion = cargoNix.internal.crates."opencode-desktop".version;
  cargoNixVersionCheck =
    if cargoNixVersion == cargoManifestVersion then
      true
    else
      throw ''
        packages/opencode-desktop/Cargo.nix has opencode-desktop version ${cargoNixVersion},
        expected ${cargoManifestVersion}; regenerate Cargo.nix
      '';

  desktopPackageVersionCheck =
    if version == desktopPackageVersion || lib.hasPrefix "${desktopPackageVersion}-" version then
      true
    else
      throw ''
        packages/opencode-desktop/default.nix has desktop version ${version},
        expected ${desktopPackageVersion} or ${desktopPackageVersion}-<suffix>
      '';

  rootCrateOverride = _attrs: {
    inherit pname version;
    src = patchedSrc;
    dontCargoSetupPostUnpack = true;
    cargoVendorDir = ".";
    setSourceRoot = ''
      sourceRoot="$(echo ./*/packages/desktop/src-tauri)"
    '';

    inherit (upstreamDesktop)
      patches
      nativeBuildInputs
      buildInputs
      strictDeps
      meta
      ;

    preConfigure = ''
      repoRoot="$(realpath ../../..)"
      srcTauriRoot="$repoRoot/packages/desktop/src-tauri"

      chmod -R u+w "$repoRoot"
      cp -a ${opencode.node_modules}/{node_modules,packages} "$repoRoot"
      chmod -R u+w "$repoRoot/node_modules" "$repoRoot/packages"
      patchShebangs "$repoRoot/node_modules"
      patchShebangs "$repoRoot/packages/desktop/node_modules"

      install -Dm755 \
        ${opencode}/bin/opencode \
        "$srcTauriRoot/sidecars/opencode-cli-${stdenv.hostPlatform.rust.rustcTarget}"
    '';

    tauriBuildFlags = [
      "--config"
      "tauri.prod.conf.json"
      "--no-sign"
    ];

    postFixup = lib.optionalString stdenv.hostPlatform.isLinux ''
      if [ -e "$out/bin/OpenCode" ]; then
        mv "$out/bin/OpenCode" "$out/bin/opencode-desktop"
      fi

      if [ -e "$out/share/applications/OpenCode.desktop" ]; then
        sed -i 's|^Exec=OpenCode$|Exec=opencode-desktop|' \
          "$out/share/applications/OpenCode.desktop"
      fi
    '';
  };

  crateOverrides =
    pkgs.defaultCrateOverrides
    // {
      opencode-desktop = rootCrateOverride;
    }
    // slib.mkCrate2nixTauriOverrides { inherit pkgs; };

  opencodeDesktopDrv = cargoNix.workspaceMembers.opencode-desktop.build.override {
    inherit crateOverrides;
    runTests = false;
  };
in
assert cargoNixVersionCheck;
assert desktopPackageVersionCheck;
opencodeDesktopDrv.overrideAttrs (old: {
  __intentionallyOverridingVersion = true;
  inherit pname version;
  inherit (upstreamDesktop) meta;
  passthru = (old.passthru or { }) // {
    inherit cargoNix crateOverrides patchedSrc;
    inherit opencodeDesktopDrv;
  };
})
