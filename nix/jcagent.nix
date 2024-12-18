{
  config,
  lib,
  stdenvNoCC,
  bintools,
  ...
}:
with lib;
let
  cfg = config.services.jcagent;
  version = "1.255.0";
  package = stdenvNoCC.mkDerivation {
    name = "jcagent";
    inherit version;
    src = "https://cdn02.jumpcloud.com/production/versions/${version}/jcagent-linux-deb-x86_64.deb";

    sourceRoot = ".";

    unpackPhase = ''
      runHook preUnpack

      ar x $src
      mkdir -p extracted
      tar -C extracted xf data.tar.gz

      runHook postUnpack
    '';

    installPhase = ''
      runHook preInstall

      mkdir $out
      cp -r extracted $out

      runHook postInstall
    '';
  };
in
{
  options.services.jcagent.enable = mkEnableOption "Enable JumpCloud Agent service";

  config = mkIf cfg.enable {
    environment.systemPackages = [ package ];
    systemd.services.jcagent = { };
  };
}
