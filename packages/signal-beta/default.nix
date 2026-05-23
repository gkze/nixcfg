{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  lib,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "signal-beta";
  appName = "Signal Beta";
  executableName = "Signal Beta";
  info = selfSource;
  description = "Private messenger desktop app beta";
  homepage = "https://signal.org/download/";
  license = lib.licenses.gpl3Only;
}
