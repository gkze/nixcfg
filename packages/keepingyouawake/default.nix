{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  lib,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "keepingyouawake";
  appName = "KeepingYouAwake";
  info = selfSource;
  description = "Menu bar utility that prevents macOS sleep";
  homepage = "https://keepingyouawake.app/";
  license = lib.licenses.mit;
}
