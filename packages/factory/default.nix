{
  mkDmgApp,
  mkSimpleDarwinApp,
  selfSource,
  ...
}:
mkSimpleDarwinApp {
  builder = mkDmgApp;
  pname = "factory";
  appName = "Factory";
  executableName = "factory-desktop";
  info = selfSource;
  description = "Native AI agent workspace from Factory";
  homepage = "https://www.factory.ai/";
}
