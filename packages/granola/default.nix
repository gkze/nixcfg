{
  mkZipApp,
  selfSource,
  lib,
  ...
}:
mkZipApp {
  pname = "granola";
  appName = "Granola";
  info = selfSource;
  meta = with lib; {
    description = "AI meeting notes app for macOS";
    homepage = "https://granola.ai/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "granola";
  };
}
