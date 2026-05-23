{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "granola";
  appName = "Granola";
  info = selfSource;
  dontFixup = false;
  description = "AI meeting notes app for macOS";
  homepage = "https://granola.ai/";
}
