{
  description = "Universe";

  inputs = {
    # Use latest nixpkgs
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # Nix User Repository
    nur.url = "github:nix-community/NUR";

    # Flake helper
    fp.url = "github:hercules-ci/flake-parts";

    # Manage macOS configuration
    nix-darwin = {
      url = "github:LnL7/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Manage $HOME environment
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

    # Neovim configured with Nix
    nixvim = {
      url = "github:nix-community/nixvim";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Nix editor
    nix-editor = {
      url = "github:vlinkz/nix-editor";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # NixOS image creation
    nixos-generators = {
      url = "github:nix-community/nixos-generators";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Declarative disk partitioning
    # TODO: use
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Run unpatched binaries on Nix/NixOS
    nix-alien = {
      url = "github:thiagokokada/nix-alien";
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

    # Hardware-specific settings
    nixos-hardware.url = "github:NixOS/nixos-hardware/master";

    ### Non-flake inputs

    # Alacritty themes
    alacritty-theme = {
      url = "github:alacritty/alacritty-theme";
      flake = false;
    };

    # Binary manager
    bin = {
      url = "github:marcosnils/bin/v0.17.4";
      flake = false;
    };

    # Neovim proportional buffer dimensions
    bufresize-nvim = {
      url = "github:kwkarlwang/bufresize.nvim";
      flake = false;
    };

    # Catppuccin theme
    catppuccin.url = "github:catppuccin/nix";

    # Code snapshotting plugin
    codesnap-nvim = {
      url = "github:mistricky/codesnap.nvim";
      flake = false;
    };

    # Web browser
    firefox = {
      url = "github:nix-community/flake-firefox-nightly";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Browser Neovim client
    firenvim = {
      url = "github:glacambre/firenvim";
      flake = false;
    };

    # GNOME Wayland-based GPU-accelerated terminal emulator - Catppuccin theme
    foot-catppuccin = {
      url = "github:catppuccin/foot";
      flake = false;
    };

    # Git branch cleanup tool
    git-trim = {
      url = "github:jasonmccreary/git-trim";
      flake = false;
    };

    # GitLab Neovim Plugin
    gitlab-nvim = {
      url = "github:gkze/gitlab.nvim";
      flake = false;
    };

    # Virtual KVM
    # lan-mouse = {
    #   url = "github:feschber/lan-mouse";
    #   inputs.nixpkgs.follows = "nixpkgs";
    # };

    # LSP signature help
    lsp-signature-nvim = {
      url = "github:ray-x/lsp_signature.nvim";
      flake = false;
    };

    # Vim text alignment plugin
    mini-align = {
      url = "github:echasnovski/mini.align";
      flake = false;
    };

    # Rust in Nix build tool
    naersk.url = "github:nix-community/naersk";

    # Neovim database UI
    nvim-dbee = {
      url = "github:kndndrj/nvim-dbee";
      flake = false;
    };

    # Neovim structured editing plugin
    # TODO: fix attempt to index nil value" 
    # @ https://github.com/Dkendal/nvim-treeclimber/blob/613daac29f134ad66ccc20f3445d35645a7fe17e/lua/nvim-treeclimber.lua#L29
    nvim-treeclimber = {
      url = "github:Dkendal/nvim-treeclimber";
      flake = false;
    };

    # In-editor Markdown rendering for Neovi
    render-markdown-nvim = {
      url = "github:MeanderingProgrammer/render-markdown.nvim";
      flake = false;
    };

    # SQL linter & formatter
    sqruff = {
      url = "github:quarylabs/sqruff/v0.19.1";
      flake = false;
    };

    # Sublime syntax for KDL (used in bat)
    sublime-kdl = {
      url = "github:eugenesvk/sublime-KDL";
      flake = false;
    };

    # Aesthetic modern terminal file manager
    superfile = {
      url = "github:MHNightCat/superfile";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Rust-based Python package resolver & installed (faster pip)
    uv = {
      url = "github:astral-sh/uv/0.4.9";
      flake = false;
    };

    # Vim Mako (template language) syntax
    vim-bundle-mako = {
      url = "github:sophacles/vim-bundle-mako";
      flake = false;
    };

    # Yet Another AWS SSO - sync AWS SSO session to legacy v1 creds
    yawsso = {
      url = "github:victorskl/yawsso";
      flake = false;
    };

    # Terminal multiplexer and workspace manager
    # zellij = {
    #   url = "github:zellij-org/zellij/a88b34f54f053e556031c3d8873be58df45e65e2";
    #   flake = false;
    # };
  };

  outputs =
    inputs:
    let
      # Grab some builtins into our lexical scope
      inherit (builtins) elemAt split;

      # Main user
      username = "george";

      # Shorthand for dprint WASM plugin URLs
      dprintWasmPluginUrl = n: v: "https://plugins.dprint.dev/${n}-${v}.wasm";

      # One function to declare both NixOS and Darwin system config
      mkSystem = import ./lib/mksystem.nix;

      # Our Nixpkgs
      mkNixpkgs =
        system:
        import inputs.nixpkgs {
          inherit system;
          config = {
            allowUnfree = true;
            allowInsecure = true;
            permittedInsecurePackages = [ "nix-2.25.0pre20240807_cfe66dbe" ];
          };
          overlays =
            (with inputs; [
              devshell.overlays.default
              nix-alien.overlays.default
              nur.overlay
              rust-overlay.overlays.default
            ])
            ++ [ (import ./nix/overlays.nix { inherit inputs system; }) ];
        };
    in
    inputs.fp.lib.mkFlake { inherit inputs; } {
      # All officially supported systems
      systems = inputs.nixpkgs.lib.systems.flakeExposed;

      # Attributes here have systeme above suffixed across them
      perSystem =
        {
          system,
          pkgs,
          lib,
          ...
        }:
        let
          # Cross-platform configuration rebuild action
          rebuild = {
            type = "app";
            program =
              {
                "darwin" = inputs.nix-darwin.packages.${system}.darwin-rebuild + "/bin/darwin-rebuild";
                "linux" = pkgs.nixos-rebuild + "/bin/nixos-rebuild";
              }
              .${elemAt (split "-" system) 2};
          };
        in
        {
          # Inject Nixpkgs with our config
          # https://nixos.org/manual/nixos/unstable/options#opt-_module.args
          _module.args.pkgs = mkNixpkgs system;

          # Unified source formatting
          formatter = inputs.treefmt-nix.lib.mkWrapper pkgs {
            projectRootFile = "flake.nix";
            programs = {
              # Python
              black.enable = true;
              # Nix
              nixfmt-rfc-style.enable = true;
              deadnix.enable = true;
              # Shell
              shellcheck.enable = true;
              shfmt.enable = true;
              # Lua
              stylua.enable = true;
              # JSON, Markdown
              dprint = {
                enable = true;
                settings = {
                  includes = [ "**/*.{json,md}" ];
                  excludes = [ "flake.lock" ];
                  plugins = [
                    (dprintWasmPluginUrl "json" "0.19.0")
                    (dprintWasmPluginUrl "markdown" "0.16.2")
                  ];
                };
              };
            };
          };

          # Development shell
          devShells.default = pkgs.devshell.mkShell {
            name = "nixcfg";
            packages =
              (with inputs; [
                nix-editor.packages.${system}.default
                nixos-generators.packages.${system}.default
                home-manager.packages.${system}.default
              ])
              ++ (with pkgs; [
                dconf2nix
                nix-init
                nurl
              ]);
          };

          # For `nix run`
          apps = {
            # Long-form `nix run .#rebuild -- --flake . switch`
            rebuild = rebuild;
            # Short-form `nix run`
            default = rebuild // {
              program = pkgs.writeShellScriptBin "rebuild" ''
                ${rebuild.program} --flake . switch
              '';
            };
          };

          # NixOS installer ISO for Basis ThinkPad X1 Carbon
          # Only runs on an x86_64 system
          # TODO: get working
          # packages.frontier-iso = inputs.nixos-generators.nixosGenerate {
          #   inherit system;
          #   format = "install-iso";
          #   specialArgs = {
          #     hostPlatform = "x86_64-linux";
          #     users = [ username ];
          #   };
          #   modules = [
          #     ./nix/common.nix
          #     # NixOS requires this
          #     # https://search.nixos.org/options?channel=23.05&show=users.users.%3Cname%3E.isNormalUser
          #     (listToAttrs (map
          #       (u: {
          #         name = "users";
          #         value = { users.${u}.isNormalUser = true; };
          #       }) [ username ]))
          #   ];
          # };
        };

      # System-independent (sort of) attributes. They're required to be
      # top-level without a suffixed system attribute, but ironically define
      # system-specific machine configuration.
      flake = {
        # Personal MacBook Pro
        darwinConfigurations.rocinante =
          let
            arch = "aarch64";
            kernel = "darwin";
          in
          mkSystem {
            inherit inputs arch kernel;
            pkgs = mkNixpkgs "${arch}-${kernel}";
            hostName = "rocinante";
            device = "apple-macbook-pro-m1-16in";
            users = [ username ];
          };

        # TODO: Symlink merged /etc/nixos/configuration.nix for nixos-rebuild
        nixosConfigurations = {
          # Basis ThinkPad X1 Carbon
          mesa =
            let
              arch = "x86_64";
              kernel = "linux";
            in
            mkSystem {
              inherit inputs arch kernel;
              pkgs = mkNixpkgs "${arch}-${kernel}";
              hostName = "mesa";
              device = "lenovo-thinkpad-x1-carbon-gen10";
              users = [ username ];
              profiles = [ "basis" ];
            };

          # TODO:
          # # Basis ThinkPad X1 Carbon - office use
          # seriesa =
          #   let arch = "x86_64"; kernel = "linux"; in
          #   mkSystem {
          #     inherit inputs arch kernel;
          #     pkgs = mkNixpkgs "${arch}-${kernel}";
          #     hostName = "seriesa";
          #     # device = "lenovo-thinkpad-x1-extreme-gen5";
          #     users = [ username ];
          #     profiles = [ "basis" ];
          #   };
        };
      };
    };
}
