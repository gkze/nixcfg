{
  stdenvNoCC,
  fetchurl,
  outputs,
  lib,
  stdenv,
  ...
}:
let
  inherit (outputs.lib) sources;
  info = sources.droid;
  inherit (stdenv.hostPlatform) system;
  inherit (info) version;
in
stdenvNoCC.mkDerivation {
  pname = "droid";
  inherit version;

  src = fetchurl {
    url = info.urls.${system};
    hash = info.hashes.${system};
  };

  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/bin
    cp $src $out/bin/droid
    chmod +x $out/bin/droid

    runHook postInstall
  '';

  meta = with lib; {
    description = "Factory's AI coding agent";
    homepage = "https://factory.ai";
    license = licenses.unfree;
    platforms = [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "droid";
  };
}
