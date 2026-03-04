{
  mkDmgApp,
  outputs,
  lib,
  ...
}:
let
  inherit (outputs.lib) sources;
in
mkDmgApp {
  pname = "codex-desktop";
  appName = "codex";
  info = sources.codex-desktop;
  meta = with lib; {
    description = "Codex desktop app";
    homepage = "https://chatgpt.com/codex/get-started";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "codex-desktop";
  };
}
