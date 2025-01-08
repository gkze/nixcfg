{
  config,
  lib,
  pkgs,
  ...
}:
with lib;
let
  cfg = config.languages.go;
in
{
  options.languages.go.enable = mkEnableOption "go";
  config = mkIf cfg.enable {
    programs = {
      go.enable = cfg.enable;
      zsh.plugins = [
        {
          name = "go";
          src = "${pkgs.zsh-completions}/share/zsh/site-functions";
        }
      ];
    };
    home.sessionPath = [ "${config.home.homeDirectory}/go/bin" ];
  };
}
