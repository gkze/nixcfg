{ config, lib, ... }:
let
  cfg = config.nixcfg.languages.go;
in
{
  options.nixcfg.languages.go = {
    enable = lib.mkEnableOption "Go toolchain";
    binPath = lib.mkOption {
      type = lib.types.str;
      default = "${config.home.homeDirectory}/go/bin";
      description = "Path to add to PATH for Go-installed binaries.";
    };
  };

  config = lib.mkIf cfg.enable {
    programs.go.enable = true;
    home.sessionPath = [ cfg.binPath ];
  };
}
