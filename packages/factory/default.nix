{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "factory";
  bundleName = "Factory.app";
  executableName = "Factory";
  info = selfSource;
  description = "Native AI agent workspace from Factory";
  homepage = "https://www.factory.ai/";
}
