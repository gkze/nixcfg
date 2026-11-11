{
  mkDmgApp,
  selfSource,
  lib,
  ...
}:
mkDmgApp {
  pname = "town-assistant-nightly";
  # 1.62 renamed the bundle from "Town Assistant.app" to "Town.app"; the
  # bundle identifier com.town.TownAssistantMac is unchanged.
  appName = "Town";
  info = selfSource;
  macApp.installMode = "copy";
  meta = with lib; {
    description = "Nightly channel of the Town (formerly Town Assistant) macOS app";
    homepage = "https://www.town.com/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "town-assistant-nightly";
  };
}
