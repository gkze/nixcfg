{
  config,
  lib,
  pkgs,
  slib,
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
      type = types.enum [ "catppuccin" ];
      default = "catppuccin";
    };
  };

  config =
    let
      configPath =
        {
          darwin = "Library/Application Support";
          linux = ".config";
        }
        .${slib.kernel pkgs.stdenv.hostPlatform.system};
    in
    mkIf cfg.enable {
      home = {
        file."${configPath}/ptpython/config.py".source = ./ptpython.py;
        packages = with pkgs; [ (python3.withPackages cfg.packages) ];
      };
    };
}
