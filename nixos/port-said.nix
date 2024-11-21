{ src, ... }@args:
import "${src}/lib/mksystem.nix" (
  args
  // {
    systemModules = [
      (
        { homePath, ... }:
        {
          services.xserver.desktopManager.gnome.enable = true;
          users.users.jesse = {
            isNormalUser = true;
            home = "${homePath}/jesse";
            extraGroups = [ "wheel" ];
            initialPassword = "jesse";
          };
        }
      )
    ];

    users.jesse = import ../home/jesse.nix;
  }
)
