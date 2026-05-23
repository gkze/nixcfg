{
  mkDmgApp7zz,
  selfSource,
  lib,
  ...
}:
mkDmgApp7zz {
  pname = "lm-studio";
  bundleName = "LM Studio.app";
  executableName = "LM Studio";
  info = selfSource;
  createBin = true;
  meta = with lib; {
    description = "Desktop app for experimenting with local large language models";
    homepage = "https://lmstudio.ai/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "lm-studio";
  };
}
