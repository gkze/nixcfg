{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "claude";
  appName = "Claude";
  info = selfSource;
  description = "Claude desktop app";
  homepage = "https://claude.com/download";
}
