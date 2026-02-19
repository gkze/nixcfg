{
  config,
  lib,
  ...
}:
with lib;
let
  cfg = config.languages.rust;
in
{
  options.languages.rust.enable = mkEnableOption "Rust toolchain (cargo bin path)";
  config = mkIf cfg.enable {
    home.sessionPath = [ "$HOME/.cargo/bin" ];
  };
}
