{
  lib,
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "superconductor";
  bundleName = "Superconductor.app";
  executableName = "superconductor";
  info = selfSource;
  meta = {
    description = "Native macOS app for AI coding workflows";
    homepage = "https://superconductor.so/";
    license = lib.licenses.unfree;
    mainProgram = "superconductor";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
}
