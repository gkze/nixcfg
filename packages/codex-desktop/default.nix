{
  mkZipApp,
  selfSource,
  lib,
  stdenvNoCC,
  ...
}:
mkZipApp {
  pname = "codex-desktop";
  appName = "ChatGPT";
  info = selfSource;
  dontFixup = true;
  sourceName = "ChatGPT_${selfSource.version}_${stdenvNoCC.hostPlatform.system}.zip";
  meta = with lib; {
    description = "ChatGPT desktop app (unified ChatGPT and Codex)";
    homepage = "https://developers.openai.com/codex/app";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "codex-desktop";
  };
}
