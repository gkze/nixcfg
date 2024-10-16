{
  description = "Universe";

  inputs = {
    ### Flake inputs ###

    # Use latest nixpkgs
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # Flake modularization library
    flakelight = {
      url = "github:nix-community/flakelight";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Manage macOS configuration
    nix-darwin = {
      url = "github:LnL7/nix-darwin";
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

    # Neovim configured with Nix
    nixvim = {
      url = "github:nix-community/nixvim";
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

    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Hardware-specific settings. Have to track maser or pin to specific
    # commit due to lack of tags / recent releases
    nixos-hardware.url = "github:NixOS/nixos-hardware/master";

    ### Non-flake inputs ###

    # Catppuccin theme
    catppuccin.url = "github:catppuccin/nix";

    # Web browser
    firefox = {
      url = "github:nix-community/flake-firefox-nightly";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Vim syntax for KDL (used for Zellij)
    kdl-vim = {
      url = "github:imsnif/kdl.vim";
      flake = false;
    };

    # Yet Another AWS SSO - sync AWS SSO session to legacy v1 creds
    yawsso = {
      url = "github:victorskl/yawsso";
      flake = false;
    };
  };

  outputs =
    {
      flakelight,
      devshell,
      treefmt-nix,
      ...
    }:
    flakelight ./. (
      { lib, ... }:
      {
        systems = lib.systems.flakeExposed;

        nixpkgs.config.allowUnfree = true;

        withOverlays = [ devshell.overlays.default ];

        devShell =
          { pkgs, ... }:
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
              # Python
              ruff-check.enable = true;
              ruff-format.enable = true;
              # Shell
              shellcheck.enable = true;
              shfmt.enable = true;
            };
          };

        checks = { };

        nixosConfigurations = {
          mesa = {
            system = "x86_64-linux";
            modules = [
              (
                { ... }:
                {
                  system.stateVersion = builtins.readFile ./NIXOS_VERSION;
                  services.xserver = {
                    enable = true;
                    displayManager.gdm.enable = true;
                    desktopManager.gnome.enable = true;
                  };
                  users.users = {
                    root = {
                      isSystemUser = true;
                      initialPassword = "root";
                    };
                  };
                }
              )
            ];
          };
          jackson = { };
        };

        homeConfigurations = { };
      }
    );
}
