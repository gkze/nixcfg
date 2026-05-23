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
  meta = with lib; {
    description = "Open-source Kubernetes IDE";
    homepage = "https://freelens.app/";
    license = licenses.mit;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "freelens";
  };
}
