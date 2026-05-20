{
  mkZipApp,
  selfSource,
  lib,
  ...
}:
mkZipApp {
  pname = "netnewswire";
  appName = "NetNewsWire";
  info = selfSource;
  macApp = {
    installMode = "copy";
  };
  meta = with lib; {
    description = "Free and open source RSS reader for macOS";
    homepage = "https://netnewswire.com/";
    license = licenses.mit;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "netnewswire";
  };
}
