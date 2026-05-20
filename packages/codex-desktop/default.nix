{
  mkZipApp,
  selfSource,
  lib,
  stdenvNoCC,
  ...
}:
mkZipApp {
  pname = "codex-desktop";
  appName = "Codex";
  info = selfSource;
  dontFixup = true;
  sourceName = "Codex_${selfSource.version}_${stdenvNoCC.hostPlatform.system}.zip";
  meta = with lib; {
    description = "Codex desktop app";
    homepage = "https://developers.openai.com/codex/app";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "codex-desktop";
  };
}
