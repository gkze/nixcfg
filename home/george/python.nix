{
  config,
  lib,
  pkgs,
  ...
}:
with lib;
let
  cfg = config.languages.python;
in
{
  options.languages.python = {
    enable = mkEnableOption "python";
    packages = mkOption {
      type = types.functionTo (types.listOf types.package);
      default =
        pyps: with pyps; [
          ptpython
          catppuccin
        ];
    };
    ptpythonTheme = mkOption {
      type = types.oneOf [ "catppuccin" ];
      default = "catppuccin";
    };
  };

  config = mkIf cfg.enable {
    home.packages = with pkgs; [ (python3.withPackages cfg.packages) ];
    xdg.configFile."ptpython/config.py".source = ./ptpython.py;
  };
}
