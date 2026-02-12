{
  mkDmgApp,
  outputs,
  lib,
  ...
}:
let
  inherit (outputs.lib) sources;
in
mkDmgApp {
  pname = "conductor";
  info = sources.conductor;
  meta = with lib; {
    description = "Run a team of coding agents on your Mac";
    homepage = "https://www.conductor.build/";
    license = licenses.unfree;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "conductor";
  };
}
