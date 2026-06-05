{
  lib,
  mkZipApp,
  selfSource,
  ...
}:
mkZipApp {
  pname = "macai";
  bundleName = "macai.app";
  executableName = "macai";
  sourceName = "macai-${selfSource.version}.zip";
  dontFixup = true;
  info = selfSource;
  meta = with lib; {
    description = "Native AI assistant for macOS";
    homepage = "https://renset.dev/macai/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "macai";
  };
}
