{
  mkSimpleDarwinApp,
  mkZipApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkZipApp;
  pname = "arc";
  appName = "Arc";
  info = selfSource;
  description = "Web browser from The Browser Company";
  homepage = "https://arc.net/";
}
