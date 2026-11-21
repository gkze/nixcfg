{
  mkDmgApp,
  selfSource,
  lib,
  ...
}:
mkDmgApp {
  pname = "rex-beta";
  # The snapshot feed still calls itself "Rex Beta", but the bundle it ships is
  # "Rex.app" and Sparkle installs that name in place. The managed copy has to
  # use the shipped name, or a self-update leaves the Dock pinned to a bundle
  # that no longer exists.
  appName = "Rex";
  info = selfSource;
  macApp.installMode = "copy";
  meta = with lib; {
    description = "Snapshot channel of the Rex macOS terminal workspace";
    homepage = "https://superlogical.com/rex";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "rex-beta";
  };
}
