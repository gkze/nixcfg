{ config, lib, ... }:
let
  inherit (lib) mkEnableOption mkIf;
  cfg = config.profiles.work;
in
{
  options.profiles.work = {
    enable = mkEnableOption "work (Town.com) profile — adds work Homebrew casks and Mac App Store apps";
  };

  config = mkIf cfg.enable {
    homebrew = {
      casks = [
        "1password"
        "cleanshot"
        "freelens"
        "pants"
        "tailscale"
        "warp@preview"
      ];
      masApps = {
        # "iA Writer" = 775737590; # TODO: re-enable after purchasing/signing into App Store
        "Microsoft Excel" = 462058435;
        "Microsoft OneNote" = 784801555;
        "Microsoft Outlook" = 985367838;
        "Microsoft PowerPoint" = 462062816;
        "Microsoft Word" = 462054704;
      };
    };
  };
}
