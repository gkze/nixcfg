{ src, ... }@args:
import ../lib/mksystem.nix (
  args
  // {
    hostName = "mesa";
    systemModules = [
      (
        { homePath, ... }:
        {
          services.xserver.desktopManager.gnome.enable = true;
          users.users.george = {
            isNormalUser = true;
            home = "${homePath}/george";
            extraGroups = [ "wheel" ];
            initialPassword = "george";
          };
        }
      )
    ];
    users.george = import "${src}/home/george.nix";
  }
)
