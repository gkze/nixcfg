{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "grok-bot";
  appName = "Grok Bot";
  info = selfSource;
  dontFixup = true;
  description = "Desktop agent for delegating long-running coding work";
  homepage = "https://cursor.com/";
  platforms = [
    "aarch64-darwin"
    "x86_64-darwin"
  ];
}
