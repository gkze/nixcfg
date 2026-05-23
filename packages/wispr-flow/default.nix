{
  mkSimpleDarwinApp,
  mkDmgApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "wispr-flow";
  appName = "Wispr Flow";
  info = selfSource;
  dontFixup = false;
  description = "AI voice dictation app for macOS";
  homepage = "https://wisprflow.ai/";
}
