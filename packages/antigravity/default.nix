{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "antigravity";
  bundleName = "Antigravity.app";
  executableName = "Antigravity";
  info = selfSource;
  createBin = true;
  description = "Agentic development platform from Google";
  homepage = "https://antigravity.google/product/antigravity-2";
  platforms = [ "aarch64-darwin" ];
}
