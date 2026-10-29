{
  mkDmgApp,
  selfSource,
  lib,
  ...
}:
mkDmgApp {
  pname = "rex-beta";
  appName = "Rex Beta";
  # The snapshot bundle ships as "Rex Beta.app" but its Mach-O is still "Rex".
  executableName = "Rex";
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
