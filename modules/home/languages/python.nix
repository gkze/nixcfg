{
  config,
  lib,
  pkgs,
  slib,
  ...
}:
let
  cfg = config.nixcfg.languages.python;
  configPath =
    {
      darwin = "Library/Application Support";
      linux = ".config";
    }
    .${slib.kernel pkgs.stdenv.hostPlatform.system};
in
{
  options.nixcfg.languages.python = {
    enable = lib.mkEnableOption "Python runtime and REPL tools";

    interpreter = lib.mkOption {
      type = lib.types.package;
      default = pkgs.python3;
      description = "Python interpreter package used to build the user environment.";
    };

    packages = lib.mkOption {
      type = lib.types.functionTo (lib.types.listOf lib.types.package);
      default =
        pyps: with pyps; [
          ptpython
          catppuccin
        ];
      description = "Python packages to install in the user interpreter environment.";
    };

    ptpythonConfig = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = ./ptpython.py;
      description = "Optional ptpython config file path installed into the platform config directory.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.interpreter ? withPackages;
        message = "nixcfg.languages.python.interpreter must provide withPackages (e.g. pkgs.python3).";
      }
    ];

    home = {
      file = lib.mkIf (cfg.ptpythonConfig != null) {
        "${configPath}/ptpython/config.py".source = cfg.ptpythonConfig;
      };
      packages = [ (cfg.interpreter.withPackages cfg.packages) ];
    };
  };
}
