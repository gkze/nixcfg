{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "freelens";
  bundleName = "Freelens.app";
  executableName = "Freelens";
  info = selfSource;
  createBin = true;
  description = "Open-source Kubernetes IDE";
  homepage = "https://freelens.app/";
  license = lib.licenses.mit;
  platforms = [ "aarch64-darwin" ];
}
