{ config, lib, ... }:
let
  cfg = config.nixcfg.languages.bun;
in
{
  options.nixcfg.languages.bun = {
    enable = lib.mkEnableOption "Bun JavaScript runtime";
    binPath = lib.mkOption {
      type = lib.types.str;
      default = "$HOME/.bun/bin";
      description = "Path to add to PATH for bun-installed executables.";
    };
  };

  config = lib.mkIf cfg.enable {
    programs.bun.enable = true;
    home.sessionPath = [ cfg.binPath ];
  };
}
