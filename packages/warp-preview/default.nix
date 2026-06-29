{
  mkDmgApp7zz,
  selfSource,
  ...
}:
mkDmgApp7zz {
  pname = "warp-preview";
  bundleName = "WarpPreview.app";
  executableName = "preview";
  info = selfSource;
  createBin = true;
  description = "Preview channel of the Warp terminal";
  homepage = "https://www.warp.dev/";
  platforms = [ "aarch64-darwin" ];
}
