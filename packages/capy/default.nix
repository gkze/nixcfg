{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "capy";
  appName = "Capy";
  info = selfSource;
  description = "Desktop client for Capy AI coding agents";
  homepage = "https://capy.ai/";
}
