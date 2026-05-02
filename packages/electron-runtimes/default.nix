{
  lib,
  nixcfgElectron,
  stdenvNoCC,
  ...
}:
let
  versions = nixcfgElectron.versionsForSystem stdenvNoCC.hostPlatform.system;
  runtimeFor = version: nixcfgElectron.runtimeFor version;
  runtimeLinks = lib.concatMapStringsSep "\n" (version: ''
    ln -s ${runtimeFor version} "$out/runtimes/${version}"
    ln -s ${(runtimeFor version).passthru.dist} "$out/dist/${version}"
    ln -s ${(runtimeFor version).passthru.headers} "$out/headers/${version}"
  '') versions;
in
stdenvNoCC.mkDerivation {
  pname = "electron-runtimes";
  version = "nixcfg";

  dontUnpack = true;
  dontBuild = true;
  dontFixup = true;

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/runtimes" "$out/dist" "$out/headers"
    ${runtimeLinks}

    runHook postInstall
  '';

  passthru = {
    inherit versions;
    runtimes = builtins.listToAttrs (
      map (version: {
        name = version;
        value = runtimeFor version;
      }) versions
    );
  };

  meta = with lib; {
    description = "Cache target for Electron runtimes packaged by nixcfg";
    license = licenses.mit;
    platforms = [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-darwin"
      "x86_64-linux"
    ];
  };
}
