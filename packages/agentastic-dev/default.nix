{
  lib,
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "agentastic-dev";
  bundleName = "Agentastic.dev.app";
  executableName = "Agentastic.Dev";
  info = selfSource;
  meta = {
    description = "Agentic development environment for macOS";
    homepage = "https://agentastic.ai/";
    license = lib.licenses.unfree;
    mainProgram = "agentastic-dev";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
}
