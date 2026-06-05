{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "ara";
  appName = "Ara";
  info = selfSource;
  description = "AI-native desktop workspace";
  homepage = "https://ara.so/";
  platforms = [ "aarch64-darwin" ];
}
