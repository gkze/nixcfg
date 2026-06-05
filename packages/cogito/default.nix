{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "cogito";
  appName = "Cogito";
  info = selfSource;
  description = "Local-first AI workspace for macOS";
  homepage = "https://cogito.md/";
  platforms = [ "aarch64-darwin" ];
}
