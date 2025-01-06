{ config, lib, ... }:
with lib;
let
  cfg = config.languages.go;
in
{
  options.languages.go = {
    enable = mkEnableOption "go";
  };
  config = mkIf cfg.enable {
    programs.go.enable = cfg.enable;
    home.sessionPath = [ "${config.home.homeDirectory}/go/bin" ];
  };
}
