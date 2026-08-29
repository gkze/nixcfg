{
  bunSource,
  lib,
  stdenvNoCC,
  unzip,
}:
stdenvNoCC.mkDerivation {
  pname = "openchamber-bun";
  version = "1.3.14";
  src = bunSource;

  nativeBuildInputs = [ unzip ];
  sourceRoot = "bun-darwin-aarch64";

  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;

  installPhase = ''
    runHook preInstall

    install -Dm755 bun "$out/bin/bun"
    ln -s bun "$out/bin/bunx"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    test "$("$out/bin/bun" --version)" = "$version"
    /usr/bin/lipo "$out/bin/bun" -verify_arch arm64

    runHook postInstallCheck
  '';

  passthru.source = bunSource;

  meta = {
    description = "Exact Bun runtime for the OpenChamber source build";
    homepage = "https://bun.sh";
    license = lib.licenses.mit;
    mainProgram = "bun";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
}
