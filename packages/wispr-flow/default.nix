{
  mkDmgApp,
  selfSource,
  lib,
  ...
}:
mkDmgApp {
  pname = "wispr-flow";
  appName = "Wispr Flow";
  info = selfSource;
  macApp.installMode = "copy";
  meta = with lib; {
    description = "AI voice dictation app for macOS";
    homepage = "https://wisprflow.ai/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "wispr-flow";
  };
}
