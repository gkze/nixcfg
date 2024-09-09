{ config, lib, pkgs, inputs, hostPlatform, profiles, hmMods ? [ ], ... }:

# TODO: figure out and make NixOS-only (for now?)
# wayland.windowManager.hyprland.enable = true;
let
  inherit (builtins) concatStringsSep elem elemAt readFile split;
  inherit (lib) optionalString removeSuffix;
  inherit (lib.attrsets) mapAttrsToList optionalAttrs;

  # Grab the OS kernel part of the hostPlatform tuple
  kernel = elemAt (split "-" hostPlatform) 2;

  # Source code directory
  srcDir = { darwin = "Development"; linux = "src"; }.${kernel};

  # npm config file
  npmConfigFile = "${config.xdg.configHome}/npmrc";

  # User metadata
  meta = import ./meta.nix;

  # Raw GitHub Content
  ghRaw = { owner, repo, rev, path }:
    "https://raw.githubusercontent.com/${owner}/${repo}/${rev}/${path}";
in
{
  # Home Manager modules go here
  imports = [
    inputs.nixvim.homeManagerModules.nixvim
    inputs.catppuccin.homeManagerModules.catppuccin
    # inputs.lan-mouse.homeManagerModules.default
    {
      darwin = {
        # https://github.com/nix-community/home-manager/issues/1341
        home = {
          extraActivationPath = with pkgs; [ rsync dockutil gawk ];
          activation.trampolineApps = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
            ${builtins.readFile ../../lib/trampoline-apps.sh}
            fromDir="$HOME/Applications/Home Manager Apps"
            toDir="$HOME/Applications/Home Manager Trampolines"
            sync_trampolines "$fromDir" "$toDir"
          '';
        };
        # User-local launchd agents
        launchd.agents = {
          # Run /usr/bin/ssh-add (shipped by Apple with macOS by default, which
          # includes integration with macOS Keychain) and add SSH keys using macOS
          # Keychain
          ssh-add = {
            enable = true;
            config = {
              Label = "org.openssh.add";
              LaunchOnlyOnce = true;
              RunAtLoad = true;
              ProgramArguments = [
                "/usr/bin/ssh-add"
                "--apple-load-keychain"
                "--apple-use-keychain"
              ];
            };
          };

          # Start GnuPG agent
          gpg-agent = {
            enable = true;
            config = {
              Label = "org.gnupg.gpg-agent";
              RunAtLoad = true;
              ProgramArguments = [ "${pkgs.gnupg}/bin/gpg-agent" "--server" ];
            };
          };
        };
      };
      linux = {
        dconf.settings = with lib.hm.gvariant; {
          "org/gnome/desktop/background" = {
            picture-options = "zoom";
            picture-uri = "file:///home/george/.config/background";
            picture-uri-dark = "file:///home/george/.config/background";
          };

          "org/gnome/desktop/input-sources" = {
            show-all-sources = true;
            sources = [ (mkTuple [ "xkb" "us" ]) ];
            xkb-options = [ "caps:swapescape" ];
          };

          "org/gnome/desktop/interface" = {
            clock-format = "12h";
            clock-show-seconds = false;
            clock-show-weekday = true;
            color-scheme = "prefer-dark";
            cursor-theme = "catppuccin-frappe-blue-cursors";
            font-antialiasing = "grayscale";
            font-hinting = "slight";
            gtk-theme = "catppuccin-frappe-blue-standard+rimless";
            icon-theme = "Papirus-Dark";
            monospace-font-name = "Hack Nerd Font Mono 11";
            overlay-scrolling = true;
            show-battery-percentage = true;
            text-scaling-factor = 1.0;
            toolkit-accessibility = false;
          };

          "org/gnome/desktop/peripherals/touchpad" = {
            tap-to-click = true;
            two-finger-scrolling-enabled = true;
          };

          "org/gnome/desktop/wm/keybindings" = {
            move-to-center = [ "<Super><Control><Shift>Home" ];
            toggle-fullscreen = [ "<Super><Shift>F" ];
          };

          "org/gnome/desktop/wm/preferences" = {
            button-layout = "appmenu:minimize,maximize,close";
            titlebar-font = "Cantarell Bold 11";
          };

          "org/gnome/file-roller/listing" = {
            list-mode = "as-folder";
            name-column-width = 250;
            show-path = false;
            sort-method = "name";
            sort-type = "ascending";
          };

          "org/gnome/file-roller/ui" = {
            sidebar-width = 200;
            window-height = 480;
            window-width = 600;
          };

          "org/gnome/mutter" = {
            center-new-windows = true;
            dynamic-workspaces = true;
          };

          "org/gnome/nautilus/list-view" = {
            default-column-order = [
              "name"
              "size"
              "type"
              "owner"
              "group"
              "permissions"
              "where"
              "date_modified"
              "date_modified_with_time"
              "date_accessed"
              "date_created"
              "recency"
              "detailed_type"
            ];
            default-visible-columns = [
              "date_created"
              "date_modified"
              "detailed_type"
              "group"
              "name"
              "owner"
              "permissions"
              "size"
              "type"
            ];
            use-tree-view = true;
          };

          "org/gnome/settings-daemon/plugins/media-keys" = {
            next = [ "Cancel" ];
            play = [ "Messenger" ];
            previous = [ "Go" ];
          };

          "org/gnome/shell" = {
            disable-user-extensions = false;
            disabled-extensions = [
              "light-style@gnome-shell-extensions.gcampax.github.com"
              "native-window-placement@gnome-shell-extensions.gcampax.github.com"
              "window-list@gnome-shell-extensions.gcampax.github.com"
              "workspace-indicator@gnome-shell-extensions.gcampax.github.com"
            ];
            enabled-extensions = [
              "apps-menu@gnome-shell-extensions.gcampax.github.com"
              "display-brightness-ddcutil@themightydeity.github.com"
              "drive-menu@gnome-shell-extensions.gcampax.github.com"
              "places-menu@gnome-shell-extensions.gcampax.github.com"
              "screenshot-window-sizer@gnome-shell-extensions.gcampax.github.com"
              "user-theme@gnome-shell-extensions.gcampax.github.com"
            ];
            favorite-apps = [
              "beekeeper-studio.desktop"
              "obsidian.desktop"
              "firefox-nightly.desktop"
              "Alacritty.desktop"
              "slack.desktop"
              "org.gnome.Calendar.desktop"
              "org.gnome.Nautilus.desktop"
              "org.gnome.SystemMonitor.desktop"
              "org.gnome.Settings.desktop"
            ];
            last-selected-power-profile = "power-saver";
          };

          "org/gnome/shell/extensions/display-brightness-ddcutil" = {
            allow-zero-brightness = true;
            button-location = 0;
            ddcutil-binary-path = "${pkgs.ddcutil}/bin/ddcutil";
            ddcutil-queue-ms = 130.0;
            ddcutil-sleep-multiplier = 4.0;
            decrease-brightness-shortcut = [ "<Control>MonBrightnessDown" ];
            disable-display-state-check = false;
            hide-system-indicator = false;
            increase-brightness-shortcut = [ "<Control>MonBrightnessUp" ];
            only-all-slider = true;
            position-system-menu = 1.0;
            show-all-slider = true;
            show-display-name = true;
            show-osd = true;
            show-value-label = false;
            step-change-keyboard = 2.0;
            verbose-debugging = true;
          };

          "org/virt-manager/virt-manager/connections" = {
            autoconnect = [ "qemu:///system" ];
            uris = [ "qemu:///system" ];
          };
        };

        gtk = {
          enable = true;
          catppuccin = {
            enable = true;
            accent = "blue";
            flavor = "frappe";
            gnomeShellTheme = true;
            icon = { enable = true; accent = "blue"; flavor = "frappe"; };
            tweaks = [ "rimless" ];
          };
        };

        services = {
          # Activate GPG agent on Linux
          gpg-agent = { enable = true; pinentryPackage = pkgs.pinentry-gnome3; };

          # Nix shell direnv background daemon
          lorri.enable = true;
        };

        # xdg.configFile = {
        #   "gtk-4.0/assets".source = "${config.gtk.theme.package}/share/themes/${config.gtk.theme.name}/gtk-4.0/assets";
        #   "gtk-4.0/gtk.css".source = "${config.gtk.theme.package}/share/themes/${config.gtk.theme.name}/gtk-4.0/gtk.css";
        #   "gtk-4.0/gtk-dark.css".source = "${config.gtk.theme.package}/share/themes/${config.gtk.theme.name}/gtk-4.0/gtk-dark.css";
        # };
        catppuccin.pointerCursor = {
          enable = true;
          accent = "blue";
          flavor = "frappe";
        };

        # These are marked as unsupported on darwin
        home.packages = with pkgs; [
          # Database GUI
          # beekeeper-studio
          # Web browser
          # TODO: get TouchpadOverscrollHistoryNavigation working
          # (brave.overrideAttrs {
          #   postFixup = ''
          #     extraWrapperArgs='--append-flags --enable-features=TouchpadOverscrollHistoryNavigation'
          #     wrapProgram $out/bin/brave $extraWrapperArgs
          #     gappsWrapperArgs+=($extraWrapperArgs)
          #   '';
          # })
          brave
          # Universal database tool
          dbeaver-bin
          # Additional GNOME settings editing tool
          # https://wiki.gnome.org/Apps/DconfEditor
          dconf-editor
          # Display Data Channel UTILity
          ddcutil
          # Matrix client
          element-desktop
          # Gnome firmware update utility
          gnome-firmware
          # Gnome Network Displays
          gnome-network-displays
          # Additional GNOME settings tool
          gnome-tweaks
          # Productivity suite
          libreoffice
          # Unofficial userspace driver for HID++ Logitech devices
          logiops
          # Run unpatched binaries on Nix/NixOS
          nix-alien
          # TODO: TBD if works on macOS
          signal-desktop
          # Logitech device manager
          solaar
          # System Profiler
          sysprof
          # Offline documentation browser
          zeal
          # Fast and collaborative text editor
          zed
        ]
        ++ (with pkgs.gnomeExtensions; [
          # Brightness control for all detected monitors
          # Currently managed manually
          # TODO: needs direct nix store path to ddcutil - fix
          brightness-control-using-ddcutil
          # User-loadable themes from user directory
          user-themes
        ])
        ;

        programs.firefox = {
          enable = true;
          package = inputs.firefox.packages.${hostPlatform}.firefox-nightly-bin;
        };

        # wayland.windowManager.hyprland = {
        #   enable = true;
        #   settings = { };
        #   plugins = { };
        # };
      };
    }.${kernel}
  ] ++ hmMods;

  # User-level Nix config
  nix = { package = lib.mkForce pkgs.nixVersions.git; checkConfig = true; };

  catppuccin = { enable = true; accent = "blue"; flavor = "frappe"; };

  # Automatically discover installed fonts
  fonts.fontconfig.enable = true;

  home = {
    # This value determines the Home Manager release that your
    # configuration is compatible with. This helps avoid breakage
    # when a new Home Manager release introduces backwards
    # incompatible changes.
    #
    # You can update Home Manager without changing this value. See
    # the Home Manager release notes for a list of state version
    # changes in each release.
    stateVersion = removeSuffix "\n" (readFile ../../NIXOS_VERSION);

    # Shell-agnostic session $PATH
    sessionPath = [ "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/go/bin" ];

    # Shell-agnostic session environment variables
    # These only get applied on login
    sessionVariables = {
      # For when Delta uses Bat and we don't want Bat's line numbers (since we
      # use the full style by default)
      DELTA_PAGER = "bat -p";
      # Set Neovim as the default editor
      EDITOR = "nvim";
      # Set Bat with man syntax highlighting as the default man pager
      MANPAGER = "sh -c 'col -bx | bat -plman'";
      # To fix formatting for man pages
      # https://github.com/sharkdp/bat#man
      MANROFFOPT = "-c";
      # Set Bat as the Nix pager
      NIX_PAGER = "bat -p";
      # Tell npm where to look for its config file
      NPM_CONFIG_USERCONFIG = npmConfigFile;
      # Set Bat as the default pager
      PAGER = "bat -p";
      # Enable mouse support
      LESS = "-R --mouse";
    };

    # Universal cross-shell aliases
    shellAliases =
      let ezaDefaultArgs = "-albghHistype -F --color=always --icons=always"; in
      # Add aliases here
      {
        cr = "clear && reset";
        ezap = "eza ${ezaDefaultArgs}";
        ezat = "eza ${ezaDefaultArgs} --tree";
        jaws = "function() { aws $@ | jq -r '.' }";
        ne = "cd ~/.config/nixcfg && nvim";
        nv = "nvim";
        zc = "zellij action clear";
        zj = "zellij";
        zq = "zellij kill-all-sessions --yes && zellij delete-all-sessions --force --yes";
      };

    # Files/directories for $HOME
    file = {
      # We explicitly set the prefix to $HOME/.local because npm operates out 
      # of the Nix store, which is read-only
      ${npmConfigFile}.text = ''
        prefix = "''${HOME}/.local"
      '';
      # Same as above for pip
      # TODO: figure out
      # "${config.xdg.configHome}/pip/pip.conf".text = ''
      #   [install]
      #   user = true
      # '';
      "${config.xdg.configHome}/git/personal".text = ''
        [user]
          name = ${meta.name.user.github}
          email = ${meta.emails.personal}
      '';
      ".local/bin" = { source = ./bin; recursive = true; executable = true; };
    } // (optionalAttrs (elem "basis" profiles) {
      "${config.xdg.configHome}/git/basis".text = ''
        [user]
          name = ${meta.name.user.system}
          email = ${meta.emails.basis}
      '';
    });

    # Packages that should be installed to the user profile. These are just
    # installed. Programs section below both installs and configures software,
    # and is the preferred method.
    packages = with pkgs; [
      # Binary manager
      bin
      # cURL wrapper with niceties
      curlie
      # Duplicate file finder
      czkawka
      # Disk space usage analyzer (in Rust)
      du-dust
      # Envchain is a utility that loads environment variables from the system
      # keychain
      envchain
      # Alternative to `find`
      fd
      # File type identification via libmagic
      # - https://www.darwinsys.com/file/
      # - https://github.com/file/file
      file
      # GNU AWK
      gawk
      # Multiple git repository management
      # TODO: completion not working
      gita
      # Git branch maintenance tool
      git-trim
      # GitLab Command Line Interface
      glab
      # GRAPH VIsualiZer
      # Stream EDitor
      gnused
      # Tape ARchrive
      gnutar
      # Graphical Ping (ICMP)
      gping
      # HTTP client
      httpie
      # Interactive JSON filter
      # TODO: figure out
      jnv
      # Additional useful utilities (a la coreutils)
      moreutils
      # Neovim Rust GUI
      (if pkgs.stdenv.isLinux then neovide else neovide.overrideAttrs { version = "0.12.2"; })
      # Nerd Fonts
      # - https://www.nerdfonts.com/
      # - https://github.com/ryanoasis/nerd-fonts
      # Only install Hack Nerd Font, since the entire package / font repository
      # is quite large
      (nerdfonts.override { fonts = [ "Hack" ]; })
      # Container management
      # podman-desktop
      # Modern developer workflow system
      # pants
      # PostgreSQL Language Server
      postgres-lsp
      # Alternative to `ps`
      procs
      # Rust toolchain manager
      rustup
      # Synhronization utility
      rsync
      # Alternative to `sed`
      sd
      # TODO: TBD if works on macOS
      slack
      # Music streaming
      spotify
      # Fast SQL formatter
      sqruff
      # Aesthetic modern terminal file manager
      superfile
      # Code counter - enable after https://github.com/NixOS/nixpkgs/pull/268563
      tokei
      # Rust-based Python package resolver & installed (faster pip)
      uv
      # Alternative to `watch`
      viddy
      # Wayland Clipboard
      wl-clipboard
    ];
  };

  # Install and configure user-level software
  programs = {
    # Terminal emulator
    alacritty = {
      enable = true;
      package = pkgs.alacritty.overrideAttrs {
        # So that we get custom GTK themes... not ideal.
        # TODO: figure out how to use custom GTK themes with Wayland
        postFixup = "wrapProgram $out/bin/alacritty --unset WAYLAND_DISPLAY";
      };
      settings = {
        font = {
          size = lib.mkDefault 12.0;
          normal.family = "Hack Nerd Font Mono";
        };
        # Launch Zellij directly instead of going through a shell
        shell = {
          program = "${pkgs.zellij}/bin/zellij";
          # Attach to session called "main" if it exists, create one named that
          # if it doesn't
          # NOTE: this gets merged in with
          # device/home/lenovo-thinkpad-x1-carbon-gen10.nix#programs.alacritty.settings.shell.args
          # It comes last
          args = [ "attach" "--create" "main" ];
        };
      };
    };
    # Amazon Web Services Command Line Interface
    awscli.enable = true;
    # More robust alternative to `cat`
    bat = {
      enable = true;
      config = { style = "full"; theme = "Catppuccin Frappe"; };
      syntaxes.kdl = { src = pkgs.sublime-kdl; file = "KDL.sublime-syntax"; };
    };
    # System monitor
    bottom.enable = true;
    # Executes commands when changing to a directory with an `.envrc` in it
    # nix-direnv is a faster and persistent implementation of direnv's use_nix
    # and use_flake
    direnv = {
      enable = true;
      nix-direnv.enable = true;
      enableZshIntegration = true;
    };
    # `ls` alternative
    eza.enable = true;
    # Neovim configured with Nix - NEEDS TUNING
    # FuZzy Finder - finds items in lists. Current integrations / use cases:
    # - Zsh history search
    # - Neovim file picking
    fzf = { enable = true; enableZshIntegration = true; };
    # Version control
    git = {
      enable = true;
      aliases = {
        branches =
          let
            format = concatStringsSep "\t" [
              "%(color:red)%(ahead-behind:HEAD)"
              "%(color:blue)%(refname:short)"
              "%(color:yellow)%(committerdate:relative)"
              "%(color:default)%(describe)"
            ];
            header = concatStringsSep "," [
              "Ahead"
              "Behind"
              "Branch Name"
              "Last Commit"
              "Description"
            ];
          in
          concatStringsSep " " [
            "!git for-each-ref"
            "--color"
            "--sort=-committerdate"
            "--format=$'${format}'"
            "refs/heads/"
            "--no-merged"
            "|"
            "sed"
            "'s/ /\t/'"
            "|"
            "column"
            "--separator=$'\t'"
            "--table"
            "--table-columns='${header}'"
          ];
        praise = "blame";
      };
      delta = {
        enable = true;
        options = { navigate = true; side-by-side = true; };
      };
      # difftastic = { enable = true; background = "dark"; };
      extraConfig = {
        commit.gpgsign = true;
        diff.colorMoved = "default";
        fetch.prune = true;
        merge.conflictstyle = "diff3";
        rebase.pull = true;
        user.signingkey = meta.gpg.keys.personal;
      };
      includes = [
        {
          path = "${config.xdg.configHome}/git/personal";
          condition = "gitdir:~/.config/nixcfg/**";
        }
        {
          path = "${config.xdg.configHome}/git/personal";
          condition = "gitdir:~/${srcDir}/github.com/**";
        }
      ] ++ (lib.optionals (kernel == "darwin") [
        {
          path = "${config.xdg.configHome}/git/personal";
          condition = "gitdir:~/iCloud\ Drive/Development/github.com/**";
        }
      ]);
    };
    # Git terminal UI
    gitui = {
      enable = true;
      keyConfig = pkgs.fetchurl {
        url = ghRaw {
          owner = "extrawurst";
          repo = "gitui";
          rev = "c57543b4f884af31146eeee8a90e29ec69b6ef5e";
          path = "vim_style_key_config.ron";
        };
        hash = "sha256-uYL9CSCOlTdW3E87I7GsgvDEwOPHoz1LIxo8DARDX1Y=";
      };
    };
    # GitHub CLI
    gh = {
      enable = true;
      settings = {
        git_protocol = "ssh";
        editor = "nvim";
        prompt = "enabled";
        extensions = with pkgs; [ gh-dash ];
      };
    };
    # Go
    go.enable = true;
    # GnuPG
    gpg = {
      enable = true;
      homedir = "${config.xdg.dataHome}/gnupg";
      settings = {
        auto-key-retrieve = true;
        default-key = meta.gpg.keys.personal;
      };
    };
    # Post-modern editor https://helix-editor.com/ - NEEDS TUNING
    helix = {
      enable = true;
      languages.language = [
        { name = "nix"; }
        { name = "python"; }
        { name = "bash"; }
      ];
    };
    # Let Home Manager install and manage itself
    home-manager.enable = true;
    # Right now this breaks with sandbox-exec error
    # https://github.com/NixOS/nix/issues/4119
    # Java (to satisfy Visual Studio Code Java extension - possibly factor out)
    java.enable = true;
    # JSON Querier
    jq.enable = true;
    # Virtual KVM
    # lan-mouse = { enable = true; };
    # Manual page interface
    man = { enable = true; generateCaches = true; };
    nixvim = {
      # extraConfigLuaPre = ''
      #   require'plenary.profile'.start("profile.log")
      # '';
      # extraConfigLuaPost = ''
      #   require'plenary.profile'.stop()
      # '';
      enable = true;
      enableMan = true;
      colorschemes.catppuccin = {
        enable = true;
        settings = {
          flavour = "frappe";
          integrations = {
            aerial = true;
            alpha = true;
            barbecue = {
              alt_background = true;
              bold_basename = true;
              dim_context = true;
              dim_dirname = true;
            };
            cmp = true;
            dap = { enabled = true; enable_ui = true; };
            gitsigns = true;
            lsp_saga = true;
            native_lsp = { enabled = true; inlay_hints.background = true; };
            neogit = true;
            neotree = true;
            telescope.enabled = true;
            treesitter = true;
            treesitter_context = true;
            which_key = true;
          };
          show_end_of_buffer = true;
          term_colors = true;
        };
      };
      # Editor-agnostic configuration
      editorconfig.enable = true;
      # Remap leader key to spacebar
      globals.mapleader = " ";
      # Set Space key to be leader
      options = {
        # Text width helper
        colorcolumn = [ 80 100 ];
        # Highlight cursor line
        cursorline = true;
        # Highlight cursor column
        cursorcolumn = true;
        # Mouse
        mouse = "a";
        # Rulers at 80 and 100 characters
        # Line numbers
        number = true;
        # List mode (display non-printing characters)
        list = true;
        # Set printing characters for non-printing characters
        listchars = {
          eol = "↵";
          extends = ">";
          nbsp = "°";
          precedes = "<";
          space = "·";
          tab = ">-";
          trail = ".";
        };
        # Keep sign column rendered so that errors popping up don't trigger a
        # redraw
        signcolumn = "yes";
      };
      plugins = {
        # Greeter (home page)
        alpha = {
          enable = true;
          layout =
            let
              button = val: shortcut: cmd: {
                type = "button";
                inherit val;
                on_press.__raw = "function() vim.cmd[[${cmd}]] end";
                opts = {
                  inherit shortcut;
                  align_shortcut = "right";
                  keymap = [ "n" shortcut ":${cmd}<CR>" { } ];
                  position = "center";
                  width = 50;
                };
              };
              padding = v: { type = "padding"; val = v; opts.position = "center"; };
            in
            [
              (padding 2)
              {
                type = "text";
                val = [
                  "███╗   ██╗██╗██╗  ██╗██╗   ██╗██╗███╗   ███╗"
                  "████╗  ██║██║╚██╗██╔╝██║   ██║██║████╗ ████║"
                  "██╔██╗ ██║██║ ╚███╔╝ ██║   ██║██║██╔████╔██║"
                  "██║╚██╗██║██║ ██╔██╗ ╚██╗ ██╔╝██║██║╚██╔╝██║"
                  "██║ ╚████║██║██╔╝ ██╗ ╚████╔╝ ██║██║ ╚═╝ ██║"
                  "╚═╝  ╚═══╝╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚═╝     ╚═╝"
                ];
                opts = { position = "center"; hl = "Type"; };
              }
              (padding 2)
              {
                type = "group";
                val = [
                  (button " New file" "e" "ene")
                  (padding 1)
                  (button "󰈞 Find file(s)" "f" "Telescope find_files")
                  (padding 1)
                  (button "󰈞 Find text" "t" "Telescope live_grep")
                  (padding 1)
                  (button " Quit Neovim" "q" "qall")
                ];
              }
              (padding 2)
              {
                type = "text";
                val = "Crankenstein";
                opts = { position = "center"; hl = "Keyword"; };
              }
            ];
        };
        # Buffer line (top, tabs)
        bufferline = {
          enable = true;
          settings.options = {
            diagnostics = "nvim_lsp";
            enforce_regular_tabs = false;
            offsets = [{
              filetype = "neo-tree";
              text = "Neo-tree";
              separator = true;
              textAlign = "left";
            }];
          };
        };
        # LSP completion
        cmp = {
          enable = true;
          settings = {
            extraOptions.autoEnableSources = true;
            snippet.expand = ''
              function(args)
                require('luasnip').lsp_expand(args.body)
              end
            '';
            sources = map (s: { name = s; }) [
              "nvim_lsp"
              "treesitter"
              "luasnip"
              "path"
              "buffer"
            ];
            mapping = {
              "<C-d>" = "cmp.mapping.scroll_docs(-4)";
              "<C-f>" = "cmp.mapping.scroll_docs(4)";
              "<C-Space>" = "cmp.mapping.complete()";
              "<C-e>" = "cmp.mapping.close()";
              "<Tab>" = "cmp.mapping(cmp.mapping.select_next_item(), {'i', 's'})";
              "<S-Tab>" = "cmp.mapping(cmp.mapping.select_prev_item(), {'i', 's'})";
              "<CR>" = "cmp.mapping.confirm({ select = true })";
            };
          };
        };
        # Formatting
        conform-nvim = {
          enable = true;
          settings = {
            formatters_by_ft.typescript = [ "prettier" ];
            format_on_save.lsp_format = "fallback";
          };
        };
        # Git information
        gitsigns = {
          enable = true;
          settings = {
            current_line_blame = true;
            current_line_blame_opts.delay = 300;
          };
        };
        # Language Server Protocol client
        lsp = {
          enable = true;
          keymaps.lspBuf = {
            "<C-k>" = "signature_help";
            "K" = "hover";
            "gD" = "references";
            "gd" = "definition";
            "gi" = "implementation";
            "gt" = "type_definition";
          };
          servers = {
            # Nix (nil with nixpkgs-fmt)
            # TODO: determine if nil or nixd is better
            # nixd = {
            #   enable = true;
            #   settings.formatting.command = "${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt";
            # };
            nil-ls = {
              enable = true;
              settings.formatting.command = [ "${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt" ];
            };
            bashls.enable = true;
            # TypeScript & JavaScript
            # TODO: Re-enable at some point...
            biome.enable = false;
            cssls.enable = true;
            dockerls.enable = true;
            # Generic language server proxy for multiple tools
            efm.enable = true;
            eslint.enable = true;
            gopls.enable = true;
            html.enable = true;
            jsonls.enable = true;
            pyright.enable = true;
            ruff-lsp.enable = false;
            rust-analyzer = {
              enable = true;
              installCargo = true;
              installRustc = true;
            };
            # TOML
            taplo.enable = true;
            tailwindcss.enable = true;
            # tsserver.enable = true;
            typos-lsp.enable = true;
            yamlls = {
              enable = true;
              extraOptions.settings.yaml.customTags = [
                "!Base64"
                "!Cidr"
                "!FindInMap"
                "!ForEach"
                "!GetAZs"
                "!GetAtt"
                "!ImportValue"
                "!Join"
                "!Length"
                "!Ref"
                "!Select"
                "!Split"
                "!Sub"
                "!ToJsonString"
                "!Transform"
              ];
            };
          };
        };
        # Status line (bottom)
        lualine = {
          enable = true;
          componentSeparators = { left = ""; right = ""; };
          sectionSeparators = { left = ""; right = ""; };
        };
        # File explorer
        neo-tree = {
          enable = true;
          closeIfLastWindow = true;
          filesystem = {
            filteredItems = { hideDotfiles = false; hideGitignored = false; };
            followCurrentFile = { enabled = true; leaveDirsOpen = true; };
            useLibuvFileWatcher = true;
          };
          sourceSelector.winbar = true;
          window.mappings = { "<A-S-{>" = "prev_source"; "<A-S-}>" = "next_source"; };
        };
        # Display colors for color codes
        nvim-colorizer = {
          enable = true;
          fileTypes = [{ language = "typescriptreact"; tailwind = "both"; }];
        };
        # File finder (popup)
        telescope = {
          enable = true;
          settings.defaults.layout_config.preview_width = 0.5;
        };
        # Built-in terminal
        toggleterm = {
          enable = true;
          settings = {
            size = 10;
            float_opts = { height = 45; width = 170; };
          };
        };
        # Parser generator & incremental parsing toolkit
        treesitter = {
          enable = true;
          folding = false;
          nixvimInjections = true;
          settings = {
            highlight = {
              enable = true;
              additional_vim_regex_highlighting = true;
            };
            incremental_selection.enable = true;
          };
        };
        # Tree-sitter text objects
        # TODO: figure out
        treesitter-textobjects = {
          enable = true;
          lspInterop.enable = true;
          move = {
            enable = true;
            gotoNextStart = {
              "]]" = "@class.outer";
              "]m" = "@function.outer";
              "]v" = "@assignment.outer";
              "]c" = "@call.outer";
              "]b" = "@block.outer";
              "]s" = "@statement.outer";
              "]i" = "@conditional.outer";
            };
            gotoNextEnd = {
              "]M" = "@function.outer";
              "][" = "@class.outer";
              "]V" = "@assignment.outer";
              "]C" = "@call.outer";
              "]B" = "@block.outer";
              "]S" = "@statement.outer";
              "]I" = "@conditional.outer";
            };
            gotoPreviousStart = {
              "[[" = "@class.outer";
              "[m" = "@function.outer";
              "[v" = "@assignment.outer";
              "[c" = "@call.outer";
              "[b" = "@block.outer";
              "[s" = "@statement.outer";
              "[i" = "@conditional.outer";
            };
            gotoPreviousEnd = {
              "[M" = "@function.outer";
              "[]" = "@class.outer";
              "[V" = "@assignment.outer";
              "[C" = "@call.outer";
              "[B" = "@block.outer";
              "[S" = "@statement.outer";
              "[I" = "@conditional.outer";
            };
          };
          select = {
            enable = true;
            lookahead = true;
            keymaps = {
              "aC" = "@class.outer";
              "aa" = "@parameter.outer";
              "ab" = "@block.outer";
              "ac" = "@call.outer";
              "af" = "@function.outer";
              "ai" = "@conditional.outer";
              "al" = "@loop.outer";
              "av" = "@assignment.outer";
              "iC" = "@class.inner";
              "ia" = "@parameter.inner";
              "ib" = "@block.inner";
              "ic" = "@call.inner";
              "if" = "@function.inner";
              "ii" = "@conditional.inner";
              "il" = "@loop.inner";
              "iv" = "@assignment.inner";
              "lv" = "@assignment.lhs";
              "rv" = "@assignment.rhs";
            };
          };
        };
        # The TypeScript integration NeoVim deserves
        typescript-tools = { enable = true; settings.exposeAsCodeAction = "all"; };
        # File / AST breadcrumbs
        barbecue.enable = true;
        # nvim-cmp LSP signature help source
        # cmp-nvim-lsp-signature-help.enable = true;
        # Treesitter completion source for CMP
        cmp-treesitter.enable = true;
        # Code commenting
        comment.enable = true;
        # GitHub Copilot coding assistant
        copilot-vim.enable = true;
        # Debug Adapter Protocol
        dap.enable = true;
        # Diff view
        diffview.enable = true;
        # LSP & notification UI
        fidget.enable = true;
        # Shareable file permalinks
        gitlinker.enable = true;
        # Highlight other occurrences of word under cursor
        illuminate.enable = true;
        # Incremental rename
        inc-rename.enable = true;
        # Indentation guide
        indent-blankline.enable = true;
        # Snippet engine
        luasnip.enable = true;
        # LSP formatting
        lsp-format.enable = true;
        # LSP pictograms
        lspkind.enable = true;
        # Multi-faceted LSP UX improvements
        lspsaga.enable = true;
        # Markdown preview
        markdown-preview.enable = true;
        # (Neo)Vim markers enhancer
        marks.enable = true;
        # Mini library collection - alignment
        mini.modules.align = { };
        # Symbol navigation popup
        navbuddy = { enable = true; lsp.autoAttach = true; };
        # Neovim git interface
        neogit = { enable = true; settings.integrations.diffview = true; };
        # Enable Nix language support
        nix.enable = true;
        # Automatically manage character pairs
        nvim-autopairs.enable = true;
        # File explorer
        oil.enable = true;
        # Schemastore
        schemastore.enable = true;
        # Better split management
        smart-splits.enable = true;
        # Enable working with TODO: code comments
        todo-comments.enable = true;
        # Code context via Treesitter
        # treesitter-context.enable = true;
        # Diagnostics, etc. 
        trouble.enable = true;
        # Keybinding hint viewer
        which-key.enable = true;
      };
      autoCmd = [
        {
          event = [ "LspAttach" ];
          callback.__raw = ''
            function(args)
              local bufnr = args.buf
              local client = vim.lsp.get_client_by_id(args.data.client_id)
              require("lsp_signature").on_attach({ bind = true }, bufnr)
            end
          '';
        }
      ];
      extraPlugins = with pkgs.vimPlugins; [
        aerial-nvim
        bufdelete-nvim
        bufresize-nvim
        # codesnap-nvim
        firenvim
        git-conflict-nvim
        gitlab-nvim
        lsp-signature-nvim
        nvim-dbee
        nvim-surround
        nvim-treeclimber
        nvim-treesitter-textsubjects
        overseer-nvim
        vim-bazel
        vim-bundle-mako
        vim-jinja
      ];
      extraConfigLua =
        let
          helpers = inputs.nixvim.lib.${hostPlatform}.helpers;
          extraPluginsConfig = {
            # TODO: enable once figured out
            # bufresize = { };
            # codesnap = {
            #   code_font_family = "Hack Nerd Font Mono";
            #   has_breadcrumbs = true;
            #   save_path = "~/Pictures";
            #   watermark = "";
            # };
            git-conflict = { };
            gitlab = { };
            nvim-surround = { };
            nvim-treeclimber = { };
            overseer = { };
            aerial = {
              autojump = true;
              filter_kind = false;
              open_automatic = true;
            };
            "nvim-treesitter.configs".textsubjects = {
              enable = true;
              rrev_selection = ",";
              keymaps = {
                "." = "textsubjects-smart";
                ";" = "textsubjects-container-outer";
                "i;" = "textsubjects-container-inner";
              };
            };
          };
        in
        (concatStringsSep "\n"
          ((mapAttrsToList (n: v: "require(\"${n}\").setup(${helpers.toLuaObject v})") extraPluginsConfig)
            ++ [
            "if vim.g.neovide then vim.g.neovide_scale_factor = 0.7 end"
            ''
              lspconfig = require('lspconfig')
              lspconfig.postgres_lsp.setup({
                root_dir = lspconfig.util.root_pattern 'flake.nix'
              })
            ''
          ]));
      keymaps = [
        { key = ";"; action = ":"; }
        { key = "<A-S-(>"; action = ":BufferLineMovePrev<CR>"; }
        { key = "<A-S-)>"; action = ":BufferLineMoveNext<CR>"; }
        { key = "<A-W>"; action = ":wall<CR>"; }
        { key = "<A-w>"; action = ":write<CR>"; }
        { key = "<A-w>"; action = ":write<CR>"; }
        { key = "<A-x>"; action = ":Bdelete<CR>"; }
        { key = "<A-S-{>"; action = ":BufferLineCyclePrev<CR>"; }
        { key = "<A-S-}>"; action = ":BufferLineCycleNext<CR>"; }
        { key = "<C-l>"; action = ":set invlist<CR>"; }
        { key = "<C-l>i"; action = ":LspInfo<CR>"; }
        { key = "<C-l>r"; action = ":LspRestart<CR>"; }
        { key = "<S-f>"; action = ":ToggleTerm direction=float<CR>"; } # TODO: figure out how to resize
        { key = "<S-s>"; action = ":sort<CR>"; }
        { key = "<S-t>"; action = ":ToggleTerm<CR>"; }
        { key = "<leader>D"; action = ":DiffviewClose<CR>"; }
        { key = "<leader>F"; action = ":Telescope find_files hidden=true<CR>"; }
        { key = "<leader>H"; action = ":wincmd 2h<CR>"; }
        { key = "<leader>J"; action = ":wincmd 2j<CR>"; }
        { key = "<leader>K"; action = ":wincmd 2k<CR>"; }
        { key = "<leader>L"; action = ":wincmd 2l<CR>"; }
        { key = "<leader>a"; action = ":AerialToggle<CR>"; }
        { key = "<leader>b"; action = ":Neotree toggle buffers<CR>"; }
        { key = "<leader>c"; action = ":nohlsearch<CR>"; }
        { key = "<leader>d"; action = ":DiffviewOpen<CR>"; }
        { key = "<leader>de"; action = ":TodoTelescope<CR>"; }
        { key = "<leader>dl"; action = ":TodoLocList<CR>"; }
        { key = "<leader>dr"; action = ":TodoTrouble<CR>"; }
        { key = "<leader>f"; action = ":Telescope find_files<CR>"; }
        { key = "<leader>g"; action = ":Telescope live_grep<CR>"; options.nowait = true; }
        { key = "<leader>h"; action = ":wincmd h<CR>"; }
        { key = "<leader>j"; action = ":wincmd j<CR>"; }
        { key = "<leader>k"; action = ":wincmd k<CR>"; }
        { key = "<leader>l"; action = ":wincmd l<CR>"; }
        { key = "<leader>m"; action = ":Telescope keymaps<CR>"; }
        { key = "<leader>n"; action = ":Neotree focus<CR>"; }
        { key = "<leader>p"; action = ":Trouble diagnostics<CR>"; }
        { key = "<leader>r"; action = ":Neotree reveal<CR>"; }
        { key = "<leader>rn"; action = ":IncRename "; }
        { key = "<leader>s"; action = ":Navbuddy<CR>"; }
        { key = "<leader>t"; action = ":Neotree toggle filesystem<CR>"; }
        { key = "<leader>v"; action = ":Neotree toggle git_status<CR>"; }
        { key = "<leader>x"; action = ":Neogit<CR>"; }
        { key = "<leader>z"; action = ":Neogit branch<CR>"; }
      ];
    };
    # Modern shell that focuses on structured data
    # TODO: tune
    nushell.enable = true;
    # Nixpkgs file database
    nix-index.enable = true;
    # Alternative to `grep`
    ripgrep.enable = true;
    # Secure SHell
    ssh = {
      enable = true;
      compression = true;
      forwardAgent = true;
      hashKnownHosts = true;
      matchBlocks = {
        "github.com".user = "git";
        "*".extraOptions = {
          AddKeysToAgent = "yes";
          LogLevel = "ERROR";
          StrictHostKeyChecking = "no";
        };
        # TODO: improve
        "eu.nixbuild.net".extraOptions = {
          PubkeyAcceptedKeyTypes = "ssh-ed25519";
          ServerAliveInterval = "60";
          IPQoS = "throughput";
          IdentityFile = "~/.ssh/personal_ed25519_256.pem";
          IdentityAgent = "/run/user/1000/keyring/ssh";
        };
        # TODO: improve
        "45.32.139.249".extraOptions = {
          PubkeyAcceptedKeyTypes = "ssh-ed25519";
          ServerAliveInterval = "60";
          IPQoS = "throughput";
          IdentityFile = "~/.ssh/personal_ed25519_256.pem";
          IdentityAgent = "/run/user/1000/keyring/ssh";
        };
      };
    };
    # Shell prompt
    starship = {
      enable = true;
      enableZshIntegration = true;
      enableNushellIntegration = true;
      settings = {
        add_newline = false;
        line_break.disabled = true;
        nix_shell.disabled = true;
        nodejs.disabled = true;
        python.disabled = true;
      };
    };
    # Unified software upgrader
    topgrade = {
      enable = true;
      settings = {
        git.repos = [ srcDir ];
        misc = { assume_yes = true; cleanup = true; };
      };
    };
    # Visual Studio Code - code editor / IDE
    vscode.enable = true;
    # Terminal file manager
    yazi = {
      enable = true;
      enableZshIntegration = true;
      settings.manager.sort_by = "alphabetical";
    };
    # Terminal multiplexer / workspace manager
    zellij = {
      enable = true;
      settings = {
        # TODO: set dynamically, or better yet figure out how to get Alacritty
        # to respect custom GTK themes
        env.WAYLAND_DISPLAY = "wayland-0";
        keybinds.normal = {
          "bind \"Alt s\"".Clear = { };
          "bind \"Alt L\"".GoToNextTab = { };
          "bind \"Alt H\"".GoToPreviousTab = { };
        };
        session_serialization = false;
        simplified_ui = true;
      };
    };
    # `cd` replacement that ranks frequently / recently used
    # directories for easier shorthand access
    zoxide = { enable = true; enableNushellIntegration = true; };
    # Zsh - a robust shell
    zsh = {
      enable = true;
      # Type directory names directly without `cd` to `cd` into them
      autocd = true;
      # Paths to index for the above
      cdpath = [ srcDir ];
      # $ZDOTDIR 
      dotDir = ".config/zsh";
      enableVteIntegration = true;
      history = {
        expireDuplicatesFirst = true;
        extended = true;
        ignoreAllDups = true;
        path = "${config.xdg.configHome}/zsh/history";
        save = 100000;
        share = true;
        size = 100000;
      };
      # Placed in $ZDOTDIR/.zshrc before compinit
      initExtraBeforeCompInit = ''
        # Pre-compinit
      ''
      # Darwin-only Zsh configuration (pre-compinit)
      + optionalString (kernel == "darwin") ''
        # Enable Homebrew on macOS

        # Set Homebrew prefix for M1 Mac
        HOMEBREW_PREFIX="/opt/homebrew"

        # Homebrew Zsh completion
        fpath+="''${HOMEBREW_PREFIX}/share/zsh/site-functions"
      '';
      completionInit = ''
        autoload -Uz +X compinit bashcompinit
        for dump in ''${ZDOTDIR}/.zcompdump(N.mh+24); do
          compinit && bashcompinit
        done
        compinit -C && bashcompinit -C
      '';
      # Any additional manual configuration goes here
      # Placed in $ZDOTDIR/.zshrc
      initExtra = ''
        # Post-compinit
        # Zsh builtins help
        unalias &>/dev/null run-help && autoload run-help

        # Load completion system, enable Bash completion compatibility
        zmodload zsh/complist
        autoload -Uz +X select-word-style
        select-word-style bash

        # Tune the completion system a bit
        zstyle ':completion:*' menu select
        bindkey -M menuselect '^[[Z' reverse-menu-complete
        bindkey "^R" history-incremental-search-backward

        # Keep elements of {,MAN}PATH unique
        typeset -U PATH MANPATH
      ''
      # Darwin-only Zsh configuration (post-compinit)
      + optionalString (kernel == "darwin") ''
        # For the rare case we're running on an Intel Mac
        if [[ "$(uname -m)" != "arm64" ]]; then
          HOMEBREW_PREFIX="/usr/local"
        fi

        # Just the essentials
        export PATH="''${HOMEBREW_PREFIX}/bin:''${PATH}"
        export MANPATH="''${HOMEBREW_PREFIX}/share/man:''${MANPATH}"
      '';
      plugins = [
        # IDE-like autocompletion
        # {
        #   name = "zsh-autocomplete";
        #   src = "${pkgs.zsh-autocomplete}/share/zsh-autocomplete";
        # }
        # Fish-like command autosuggestions
        {
          name = "zsh-autosuggestions";
          src = "${pkgs.zsh-autosuggestions}/share/zsh-autosuggestions";
          file = "zsh-autosuggestions.zsh";
        }
        # Fast Syntax Highlighting
        {
          name = "F-Sy-H";
          src = "${pkgs.zsh-f-sy-h}/share/zsh/site-functions";
        }
        # Improved Vi mode
        {
          name = "zsh-vi-mode";
          src = "${pkgs.zsh-vi-mode}/share/zsh-vi-mode";
        }
        # Zsh Vi mode granular backward kill word
        {
          name = "zsh-vi-mode-backward-kill-word";
          src = ./zsh-plugins;
        }
        # Use FZF for searching through history
        {
          name = "zsh-fzf-history-search";
          src = "${pkgs.zsh-fzf-history-search}/share/zsh-fzf-history-search";
        }
        # Yank selections into system clipboard
        # TODO: send Nixpkgs update PR
        {
          name = "zsh-system-clipboard";
          src = (pkgs.fetchFromGitHub {
            owner = "kutsan";
            repo = "zsh-system-clipboard";
            rev = "5f66befd96529b28767fe8a239e9c6de6d57cdc4";
            hash = "sha256-t4xPKd9BvrH4cyq8rN/IVGcm13OJNutdZ4e+FdGbPIo=";
          });
        }
        # direnv Zsh completion
        {
          name = "direnv";
          src = "${pkgs.zsh-completions}/share/zsh/site-functions";
        }
        # Go Zsh completion
        {
          name = "go";
          src = "${pkgs.zsh-completions}/share/zsh/site-functions";
        }
        # TODO: upstream fix to Nixpkgs
        {
          name = "aws";
          src = "${pkgs.awscli2}/share/zsh/site-functions";
          file = "_aws";
        }
        {
          name = "docker";
          src = pkgs.runCommand "_docker" { buildInputs = [ pkgs.docker ]; } ''
            mkdir $out
            docker completion zsh > $out/_docker
          '';
          file = "_docker";
        }
        # {
        #   name = "bin";
        #   src = pkgs.runCommand "_bin" { buildInputs = [ pkgs.bin ]; } ''
        #     mkdir $out
        #     bin completion zsh > $out/_bin
        #   '';
        #   file = "_bin";
        # }
      ];
    };
  };
}
