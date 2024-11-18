{ src, ... }@args:
import "${src}/lib/mksystem.nix" (
  args
  // {
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
    users.george = import ../home/george.nix;
  }
)
