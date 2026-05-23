{
  fetchurl,
  lib,
  selfSource,
  stdenvNoCC,
  system,
  ...
}:

stdenvNoCC.mkDerivation {
  pname = "pants-preview";
  inherit (selfSource) version;

  src = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };

  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    install -Dm755 "$src" "$out/bin/pants"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Pants launcher distributed by scie-pants";
    homepage = "https://pantsbuild.org/";
    license = licenses.asl20;
    platforms = [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "pants";
  };
}
