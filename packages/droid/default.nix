{
  stdenvNoCC,
  fetchurl,
  selfSource,
  lib,
  stdenv,
  ...
}:
let
  inherit (stdenv.hostPlatform) system;
in
stdenvNoCC.mkDerivation {
  pname = "droid";
  inherit (selfSource) version;

  src = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
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
      "x86_64-darwin"
      "x86_64-linux"
    ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "droid";
  };
}
