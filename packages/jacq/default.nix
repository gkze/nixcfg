{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "jacq";
  appName = "Jacq";
  info = selfSource;
  description = "AI coding companion for macOS";
  homepage = "https://jacquard.dev/";
  platforms = [ "aarch64-darwin" ];
}
