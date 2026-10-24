{
  prev,
  selfSource,
  system,
  ...
}:
let
  inherit (prev) lib;
  version = selfSource.version;
  # Official release zips. The nixpkgs Bun derivation unpacks these layouts;
  # its sourceRoot is only special-cased for aarch64-darwin, so the pin stays
  # on the systems that derivation already knows how to install.
  releaseSources = lib.mapAttrs (
    targetSystem: hash:
    prev.fetchurl {
      url = selfSource.urls.${targetSystem};
      inherit hash;
    }
  ) selfSource.hashes;
in
{
  # One Bun for the package set. The updater in ./updater.py follows
  # oven-sh/bun releases and rewrites sources.json. Lockfile installers still
  # compare packageManager with this version before they use it.
  bun = prev.bun.overrideAttrs (old: {
    inherit version;
    src = releaseSources.${system} or (throw "nixcfg bun has no upstream release binary for ${system}");

    passthru = removeAttrs (old.passthru or { }) [ "updateScript" ] // {
      sources = releaseSources;
    };

    doInstallCheck = true;
    # $version follows overrideAttrs, so a package that swaps in another
    # release still checks the binary it actually installed.
    installCheckPhase = ''
      runHook preInstallCheck
      test "$($out/bin/bun --version)" = "$version"
      runHook postInstallCheck
    '';
  });
}
