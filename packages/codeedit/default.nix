{
  mkSimpleDarwinApp,
  mkDmgApp,
  selfSource,
  lib,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "codeedit";
  appName = "CodeEdit";
  info = selfSource;
  description = "Code editor for macOS";
  homepage = "https://www.codeedit.app/";
  license = lib.licenses.mit;
}
