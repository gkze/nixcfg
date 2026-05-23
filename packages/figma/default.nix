{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "figma";
  appName = "Figma";
  info = selfSource;
  description = "Collaborative interface design tool";
  homepage = "https://www.figma.com/downloads/";
}
