{
  claude-code,
  lib,
  stdenvNoCC,
  ...
}:

stdenvNoCC.mkDerivation {
  pname = "claude-code-url-handler";
  inherit (claude-code) version;

  dontUnpack = true;
  dontBuild = true;

  infoPlist = ./Info.plist;
  claudeExecutable = lib.getExe claude-code;
  installPhase = ./install.sh;

  doInstallCheck = true;
  installCheckPhase = ./check.sh;

  passthru = {
    macApp = {
      bundleName = "Claude Code URL Handler.app";
      bundleRelPath = "Applications/Claude Code URL Handler.app";
      installMode = "copy";
    };
  };

  meta = with lib; {
    description = "Background handler for Claude Code deep links";
    homepage = claude-code.meta.homepage;
    license = claude-code.meta.license;
    platforms = claude-code.meta.platforms;
    sourceProvenance = with sourceTypes; [ fromSource ];
  };
}
