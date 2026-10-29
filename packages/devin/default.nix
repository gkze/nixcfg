{
  mkZipApp,
  selfSource,
  lib,
  ...
}:
mkZipApp {
  pname = "devin";
  appName = "Devin";
  info = selfSource;
  macApp.installMode = "copy";
  meta = with lib; {
    description = "Devin Desktop, an agent command center for local and cloud agents";
    homepage = "https://devin.ai/desktop";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "devin";
  };
}
