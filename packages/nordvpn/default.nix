{
  lib,
  mkPkgApp,
  selfSource,
  ...
}:

mkPkgApp {
  pname = "nordvpn";
  info = selfSource;
  bundleName = "NordVPN.app";
  executableName = "NordVPN";

  description = "NordVPN client for macOS";
  homepage = "https://nordvpn.com/";
  license = lib.licenses.unfree;
  platforms = [ "aarch64-darwin" ];
  sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  mainProgram = "nordvpn";
}
