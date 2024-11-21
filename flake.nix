{
  description = "Universe";

  inputs = {
    ### Flake inputs ###

    # Use latest nixpkgs
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";

    # Flake modularization library
    flakelight = {
      url = "github:nix-community/flakelight";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Manage user configuration and home directories
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Unified polyglot source formatter
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Nix development shell helper
    devshell = {
      url = "github:numtide/devshell";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Declarative disk partitioning
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # NixOS-specific "app store" GUI
    nix-software-center = {
      url = "github:vlinkz/nix-software-center";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Graphical NixOS configuration editor
    nixos-conf-editor = {
      url = "github:vlinkz/nixos-conf-editor";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Hardware-specific settings. Have to track maser or pin to specific
    # commit due to lack of tags / recent releases
    nixos-hardware.url = "github:NixOS/nixos-hardware/master";
  };

  outputs =
    {
      self,
      flakelight,
      devshell,
      treefmt-nix,
      ...
    }:
    flakelight ./. (
      { lib, ... }:
      {
        nixDir = ./.;
        systems = lib.systems.flakeExposed;
        nixpkgs.config.allowUnfree = true;

        withOverlays = [
          devshell.overlays.default
          self.overlays.default
        ];

        nixDirAliases = {
	  nixosConfigurations = [ "nixos" ];
          homeConfigurations = [ "home" ];
        };

        devShell =
          pkgs:
          pkgs.devshell.mkShell {
            name = "nixcfg";
            packages = with pkgs; [
              dconf2nix
              home-manager
              nh
              nix-init
              nixos-generators
              nurl
            ];
          };

        formatter =
          pkgs:
          treefmt-nix.lib.mkWrapper pkgs {
            projectRootFile = "flake.nix";
            programs = {
              # Markdown
              mdformat.enable = true;
              # Nix
              nixfmt.enable = true;
              deadnix.enable = true;
              statix.enable = true;
              # Shell
              shellcheck.enable = true;
              shfmt.enable = true;
            };
          };

        checks = { };
      }
    );
}
