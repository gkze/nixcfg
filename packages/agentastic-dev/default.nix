{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "agentastic-dev";
  bundleName = "Agentastic.dev.app";
  executableName = "Agentastic.Dev";
  info = selfSource;
  description = "Agentic development environment for macOS";
  homepage = "https://agentastic.ai/";
  platforms = [ "aarch64-darwin" ];
}
