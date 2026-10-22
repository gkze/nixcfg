{
  mkZipApp,
  selfSource,
  lib,
  ...
}:
mkZipApp {
  pname = "commander";
  appName = "Commander";
  info = selfSource;

  # Preserve the upstream code signature, including bundled shell scripts.
  dontFixup = true;
  macApp = {
    installMode = "copy";
  };

  meta = with lib; {
    description = "Native macOS workspace for AI coding agents";
    homepage = "https://thecommander.app/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "commander";
  };
}
