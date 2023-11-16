{ pkgs, lib, hostPlatform, users, ... }:
let kernel = builtins.elemAt (builtins.split "-" hostPlatform) 2; in {
  imports = [ ./system-packages.nix ];

  nix = {
    # Pin registry to system Nixpkgs
    # registry.nixpkgs.ro = { type = "path"; path = pkgs.path; };

    settings = {
      # Enable nix command and flakes
      experimental-features = [ "nix-command" "flakes" ];

      extra-platforms = [ "x86_64-darwin" ];

      # Perform builds in a sandboxed environment
      sandbox = true;
    };

    # Auto-upgrade nix command
    package = pkgs.nix;
  };

  nixpkgs = { inherit hostPlatform; config.allowUnfree = true; };

  # Install documentation for packages
  documentation = {
    doc.enable = true;
    info.enable = true;
    man.enable = true;
  };


  environment.pathsToLink = [ " /share/zsh " ];

  # Users
  users.users = builtins.listToAttrs (map
    (user: {
      name = user;
      value = {
        name = user;
        home = { darwin = "/Users/${user}"; linux = "/home/${user}"; }.${kernel};
      };
    })
    users);

  # Create /etc/zshrc that loads the nix-darwin/NixOS environment
  programs.zsh.enable = true; # default shell on Sonoma

  system.stateVersion = lib.removeSuffix "\n" (builtins.readFile ../NIXOS_VERSION);
  # system.stateVersion = 4;
}
  //
lib.attrsets.optionalAttrs (kernel == "darwin") {
  # Use a custom configuration.nix location.
  # $ darwin-rebuild switch -I darwin-config=$HOME/.config/nixcfg/configuration.nix
  environment.darwinConfig = "$HOME/.config/nixcfg/nix/common.nix";

  # Enable Toudh ID for sudo
  security.pam.enableSudoTouchIdAuth = true;

  # Auto upgrade nix package and the daemon service
  services.nix-daemon.enable = true;

  # System-wide Launch Daemons
  launchd.daemons = {
    # Raise maximum open file limit
    maxfiles.serviceConfig = {
      Label = "
        limit.maxfiles ";
      RunAtLoad = true;
      ServiceIPC = true;
      ProgramArguments = [ "launchctl" "limit" "maxfiles" "1000000" "1000000" ];
    };

    # Raise maximum running process limit
    maxproc.serviceConfig = {
      Label = "
        limit.maxproc ";
      RunAtLoad = true;
      ServiceIPC = true;
      ProgramArguments = [ "launchctl" "limit" "maxproc" "1000000" "1000000" ];
    };
  };

  # nix-darwin uses different versioning
  # https://daiderd.com/nix-darwin/manual/index.html#opt-system.stateVersion
  system.stateVersion = 4;
  # system.stateVersion = lib.removeSuffix "\n" (builtins.readFile ../NIXOS_VERSION);
}

