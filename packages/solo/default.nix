{
  lib,
  mkTgzApp,
  selfSource,
  ...
}:
mkTgzApp {
  pname = "solo";
  bundleName = "Solo.app";
  executableName = "Solo";
  info = selfSource;
  meta = {
    description = "Terminal app for solo development workflows";
    homepage = "https://soloterm.com/";
    license = lib.licenses.unfree;
    mainProgram = "solo";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
}
