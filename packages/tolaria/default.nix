{
  lib,
  mkTgzApp,
  selfSource,
  ...
}:
mkTgzApp {
  pname = "tolaria";
  bundleName = "Tolaria.app";
  executableName = "tolaria";
  info = selfSource;
  meta = {
    description = "Desktop app for Refactoring course and AI coding workflows";
    homepage = "https://refactoring.fm/";
    license = lib.licenses.unfree;
    mainProgram = "tolaria";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
}
