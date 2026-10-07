{
  mkZipApp,
  selfSource,
  lib,
  ...
}:
mkZipApp {
  pname = "codex-desktop";
  appName = "ChatGPT";
  info = selfSource;
  dontFixup = true;
  # Keep fetchurl's URL basename so validation reuses the prefetched ZIP.
  # Upstream can replace an archive when only the Sparkle build number changes.
  meta = with lib; {
    description = "ChatGPT desktop app (unified ChatGPT and Codex)";
    homepage = "https://developers.openai.com/codex/app";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "codex-desktop";
  };
}
