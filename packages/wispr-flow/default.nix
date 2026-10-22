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
  description = "AI voice dictation app for macOS";
  homepage = "https://wisprflow.ai/";
}
