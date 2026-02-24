{ config, lib, ... }:
let
  cfg = config.nixcfg.languages.rust;
in
{
  options.nixcfg.languages.rust = {
    enable = lib.mkEnableOption "Rust toolchain (cargo bin path)";
    cargoBinPath = lib.mkOption {
      type = lib.types.str;
      default = "$HOME/.cargo/bin";
      description = "Path to add to PATH for Cargo-installed binaries.";
    };
  };

  config = lib.mkIf cfg.enable {
    home.sessionPath = [ cfg.cargoBinPath ];
  };
}
