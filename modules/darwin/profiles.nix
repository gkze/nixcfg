{ config, lib, ... }:
let
  inherit (lib)
    mkIf
    mkOption
    types
    ;

  cfg = config.profiles.work;
  workProfileSkeleton = import ../_profiles-work-skeleton.nix {
    enableDescription = "work profile — adds work Homebrew casks and Mac App Store apps";
  };
in
{
  imports = [ workProfileSkeleton ];

  options.profiles.work = {
    darwin = {
      casks = mkOption {
        type = types.listOf types.str;
        default = [
          "1password"
          "cleanshot"
          "freelens"
          "pants"
          "tailscale-app"
          "warp@preview"
        ];
        description = "Homebrew casks installed when the Darwin work profile is enabled.";
      };

      masApps = mkOption {
        type = types.attrsOf types.int;
        default = {
          "iA Writer" = 775737590;
          "Microsoft Excel" = 462058435;
          "Microsoft OneNote" = 784801555;
          "Microsoft Outlook" = 985367838;
          "Microsoft PowerPoint" = 462062816;
          "Microsoft Word" = 462054704;
        };
        description = "Mac App Store applications installed when the Darwin work profile is enabled.";
      };
    };
  };

  config = mkIf cfg.enable {
    homebrew = {
      inherit (cfg.darwin)
        casks
        masApps
        ;
    };
  };
}
