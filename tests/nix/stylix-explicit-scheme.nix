{
  lib,
  src ? ../..,
  explicitScheme ? /etc/hosts,
  metadataMatches ? false,
}:
let
  evaluated = lib.evalModules {
    specialArgs = {
      inputs.base16-schemes-src = {
        rev = "input-revision";
        narHash = "sha256-input";
        outPath = "/base16-input";
        __toString = self: self.outPath;
      };
      pkgs = {
        stdenv = {
          isDarwin = true;
          isLinux = false;
        };
        base16-schemes.src = {
          rev = if metadataMatches then "input-revision" else "different-nixpkgs-revision";
          outputHash = if metadataMatches then "sha256-input" else "sha256-different-nixpkgs-source";
        };
      };
    };
    modules = [
      (src + "/modules/home/stylix.nix")
      (
        { lib, ... }:
        {
          options = {
            fonts = lib.mkOption { type = lib.types.attrs; };
            stylix = lib.mkOption { type = lib.types.attrs; };
            theme = lib.mkOption { type = lib.types.attrs; };
          };
          config = {
            fonts.monospace.size = 12;
            theme = {
              accentColor = "blue";
              polarity = "dark";
              slug = "test-theme";
              variant = "mocha";
            };
            nixcfg.stylix = {
              enableGhosttyTarget = false;
              enableIcons = false;
              enableLinuxDesktopTargets = false;
            }
            // lib.optionalAttrs (explicitScheme != null) {
              base16Scheme = explicitScheme;
            };
          };
        }
      )
    ];
  };
in
# A source/AST assertion cannot establish whether the unused default branch is lazy.
builtins.toString evaluated.config.stylix.base16Scheme
