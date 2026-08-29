{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "screen-studio";
  appName = "Screen Studio";
  info = selfSource;
  dontFixup = true;
  description = "Professional screen recorder with automatic editing effects";
  homepage = "https://screen.studio/";
  platforms = [
    "aarch64-darwin"
    "x86_64-darwin"
  ];
}
