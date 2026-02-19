{ config, lib, ... }:
let
  inherit (lib) mkOption types;
  cfg = config.theme;
  capitalize =
    s:
    (lib.toUpper (builtins.substring 0 1 s)) + (builtins.substring 1 (builtins.stringLength s - 1) s);
  # Map variant names to their accented forms where applicable
  accentedVariants = {
    frappe = "Frappé";
    latte = "Latte";
    macchiato = "Macchiato";
    mocha = "Mocha";
  };
in
{
  options.theme = {
    name = mkOption {
      type = types.str;
      default = "catppuccin";
      description = "Base theme family name.";
    };
    variant = mkOption {
      type = types.enum [
        "latte"
        "frappe"
        "macchiato"
        "mocha"
      ];
      default = "frappe";
      description = "Theme variant / flavour.";
    };
    polarity = mkOption {
      type = types.enum [
        "dark"
        "light"
      ];
      default = "dark";
      description = "Light or dark mode.";
    };
    accentColor = mkOption {
      type = types.str;
      default = "blue";
      description = "Accent color name used by cursor themes, etc.";
    };
    slug = mkOption {
      type = types.str;
      readOnly = true;
      default = "${lib.toLower cfg.name}-${lib.toLower cfg.variant}";
      description = "Lowercase slug (e.g. 'catppuccin-frappe') for tools that use kebab-case identifiers.";
    };
    displayName = mkOption {
      type = types.str;
      readOnly = true;
      default = "${capitalize cfg.name} ${capitalize cfg.variant}";
      description = "Human-readable name (e.g. 'Catppuccin Frappe') for bat, ghostty, etc.";
    };
    displayNameAccented = mkOption {
      type = types.str;
      readOnly = true;
      default = "${capitalize cfg.name} ${accentedVariants.${cfg.variant}}";
      description = "Display name with accented characters (e.g. 'Catppuccin Frappé') for Zed.";
    };
  };
}
