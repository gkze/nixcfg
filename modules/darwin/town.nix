{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    _1password-cli
    tailscale
    warp-terminal
  ];
  homebrew = {
    casks = [
      "1password"
      "cleanshot"
    ];
    masApps = {
      "Microsoft Word" = 462054704;
      "Microsoft Outlook" = 985367838;
      "Microsoft OneNote" = 784801555;
      "Microsoft Excel" = 462058435;
      "Microsoft PowerPoint" = 462062816;
    };
  };
}
