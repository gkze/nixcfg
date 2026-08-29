{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "aside";
  bundleName = "Aside.app";
  executableName = "Aside";
  info = selfSource;
  description = "AI browser with an agent for working across websites";
  homepage = "https://aside.com/";
}
