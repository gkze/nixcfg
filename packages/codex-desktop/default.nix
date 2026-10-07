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
  # Leave sourceName unset so fetchurl keeps the ZIP basename. Prepare
  # prefetches that same name into Cachix; a versioned rename misses the cache
  # and re-fetches OpenAI's short-version URL, which can change bytes before
  # Darwin closures (Update 37586805620).
  meta = with lib; {
    description = "ChatGPT desktop app (unified ChatGPT and Codex)";
    homepage = "https://developers.openai.com/codex/app";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "codex-desktop";
  };
}
