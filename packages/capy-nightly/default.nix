{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "capy-nightly";
  appName = "Capy Nightly";
  info = selfSource;
  description = "Nightly desktop client for Capy AI coding agents";
  homepage = "https://capy.ai/";
}
