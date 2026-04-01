{
  mkDmgApp,
  selfSource,
  lib,
  ...
}:
mkDmgApp {
  pname = "codex-desktop";
  appName = "codex";
  info = selfSource;
  meta = with lib; {
    description = "Codex desktop app";
    homepage = "https://chatgpt.com/codex/get-started";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "codex-desktop";
  };
}
