{
  mkSimpleDarwinApp,
  mkDmgApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "linear";
  appName = "Linear";
  info = selfSource;
  description = "Issue tracking and project planning app";
  homepage = "https://linear.app/";
}
