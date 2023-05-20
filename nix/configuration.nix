{ pkgs, users, profiles, ... }@args:
let isDarwin = pkgs.stdenv.hostPlatform.isDarwin; in
{
  imports = [
    ./system-packages.nix
    (import ./homebrew.nix (args // { inherit profiles; }))
  ];

  nix = {
    # Enable nix command and flakes
    settings.experimental-features = [ "nix-command" "flakes" ];

    # Auto-upgrade nix command
    package = pkgs.nix;
  };

  environment = {
    # Use a custom configuration.nix location.
    # $ darwin-rebuild switch -I darwin-config=$HOME/.config/nixcfg/configuration.nix
    darwinConfig = "$HOME/.config/nixcfg/nix/configuration.nix";

    pathsToLink = [ "/share/zsh" ];
  };

  # Enable Toudh ID for sudo
  security.pam.enableSudoTouchIdAuth = true;

  # System-wide launchd daemons
  launchd.daemons = {
    # Raise maximum open file limit
    maxfiles.serviceConfig = {
      Label = "limit.maxfiles";
      RunAtLoad = true;
      ServiceIPC = true;
      ProgramArguments = [ "launchctl" "limit" "maxfiles" "1000000" "1000000" ];
    };

    # Raise maximum running process limit
    maxproc.serviceConfig = {
      Label = "limit.maxproc";
      RunAtLoad = true;
      ServiceIPC = true;
      ProgramArguments = [ "launchctl" "limit" "maxproc" "1000000" "1000000" ];
    };
  };

  # Users
  users.users = builtins.listToAttrs (map
    (user: {
      name = user;
      value = {
        name = user;
        home = if isDarwin then "/Users/${user}" else "/home/${user}";
      };
    })
    users);

  # Auto upgrade nix package and the daemon service
  services.nix-daemon.enable = true;

  # Create /etc/zshrc that loads the nix-darwin environment
  programs.zsh.enable = true; # default shell on Ventura

  # System-wide settings
  # Used for backwards compatibility, please read the changelog before changing.
  # $ darwin-rebuild changelog
  system.stateVersion = 4;
}
