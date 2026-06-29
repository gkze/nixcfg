{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "yaak-beta";
  bundleName = "Yaak.app";
  executableName = "yaak-app-client";
  info = selfSource;
  createBin = true;
  description = "Beta desktop API client for REST, GraphQL, and gRPC requests";
  homepage = "https://yaak.app/";
  license = lib.licenses.mit;
  platforms = [ "aarch64-darwin" ];
}
