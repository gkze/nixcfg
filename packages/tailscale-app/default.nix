{
  lib,
  mkPkgApp,
  selfSource,
  ...
}:

mkPkgApp {
  pname = "tailscale-app";
  info = selfSource;
  bundleName = "Tailscale.app";
  executableName = "Tailscale";
  binaryName = "tailscale";
  copyContents = true;

  description = "Tailscale GUI client for macOS";
  homepage = "https://tailscale.com/";
  license = lib.licenses.unfree;
  platforms = [ "aarch64-darwin" ];
  sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  mainProgram = "tailscale";
}
