{ lib, pkgs, ... }:
let
  inherit (lib) mkOption types;
in
{
  options.fonts = {
    monospace = {
      name = mkOption {
        type = types.str;
        default = "Hack Nerd Font Mono";
        description = "Monospace font family name.";
      };
      package = mkOption {
        type = types.package;
        default = pkgs.nerd-fonts.hack;
        description = "Monospace font package.";
      };
      size = mkOption {
        type = types.int;
        default = 11;
        description = "Default monospace font size for terminal / editor UIs.";
      };
    };
    sansSerif = {
      name = mkOption {
        type = types.str;
        default = "Cantarell";
        description = "Sans-serif font family name.";
      };
      package = mkOption {
        type = types.package;
        default = pkgs.cantarell-fonts;
        description = "Sans-serif font package.";
      };
      size = mkOption {
        type = types.int;
        default = 11;
        description = "Default sans-serif font size.";
      };
    };
    serif = {
      name = mkOption {
        type = types.str;
        default = "Cantarell";
        description = "Serif font family name.";
      };
      package = mkOption {
        type = types.package;
        default = pkgs.cantarell-fonts;
        description = "Serif font package.";
      };
    };
  };
}
