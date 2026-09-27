{
  mkDmgApp,
  selfSource,
  lib,
  ...
}:
mkDmgApp {
  pname = "town-assistant-internal";
  appName = "Town Assistant";
  info = selfSource;
  macApp.installMode = "copy";
  meta = with lib; {
    description = "Internal channel of the Town Assistant macOS app";
    homepage = "https://www.town.com/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "town-assistant-internal";
  };
}
