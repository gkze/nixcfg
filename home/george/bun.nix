{
  config,
  lib,
  ...
}:
with lib;
let
  cfg = config.languages.bun;
in
{
  options.languages.bun.enable = mkEnableOption "Bun JavaScript runtime";
  config = mkIf cfg.enable {
    programs.bun.enable = true;
    home.sessionPath = [ "$HOME/.bun/bin" ];
  };
}
