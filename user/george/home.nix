{
  config,
  lib,
  pkgs,
  inputs,
  hostPlatform,
  profiles,
  hmMods ? [ ],
  ...
}:
let
  inherit (builtins)
    concatStringsSep
    elem
    elemAt
    readFile
    split
    ;
  inherit (lib) optionalString removeSuffix;
  inherit (lib.attrsets) optionalAttrs;

  # Grab the OS kernel part of the hostPlatform tuple
  kernel = elemAt (split "-" hostPlatform) 2;

  # Source code directory
  srcDir =
    {
      darwin = "Development";
      linux = "src";
    }
    .${kernel};

  # npm config file
  npmConfigFile = "${config.xdg.configHome}/npmrc";

  # User metadata
  meta = import ./meta.nix;

  # Raw GitHub Content
  ghRaw =
    {
      owner,
      repo,
      rev,
      path,
    }:
    "https://raw.githubusercontent.com/${owner}/${repo}/${rev}/${path}";
in
{
  # Home Manager modules go here
  imports = [
    inputs.nixvim.homeManagerModules.nixvim
    inputs.stylix.homeManagerModules.stylix
    ./nixvim.nix
    # inputs.lan-mouse.homeManagerModules.default
    {
      darwin = {
        # https://github.com/nix-community/home-manager/issues/1341
        home = {
          extraActivationPath = with pkgs; [
            rsync
            dockutil
            gawk
          ];
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
              ProgramArguments = [
                "${pkgs.gnupg}/bin/gpg-agent"
                "--server"
              ];
            };
          };
        };
      };
      linux = {
        imports = [ ./dconf.nix ];

        gtk = {
          enable = true;
          gtk3.extraCss = ''
            headerbar.default-decoration button.titlebutton { padding: 0; }
          '';
        };

        services = {
          # Activate GPG agent on Linux
          gpg-agent = {
            enable = true;
            pinentryPackage = pkgs.pinentry-gnome3;
          };

          # Nix shell direnv background daemon
          lorri.enable = true;
        };

        # These are marked as unsupported on darwin
        home.packages =
          with pkgs;
          [
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
            # Linux manual pages
            man-pages
            # Run unpatched binaries on Nix/NixOS
            nix-alien
            # Nix Helper CLI
            nh
            # TODO: TBD if works on macOS
            (python3.withPackages (
              ps: with ps; [
                ptpython
                catppuccin
              ]
            ))
            signal-desktop
            # Logitech device manager
            solaar
            # System Profiler
            sysprof
            # Offline documentation browser
            zeal
          ]
          ++ (with pkgs.gnomeExtensions; [
            # Brightness control for all detected monitors
            # Currently managed manually
            # TODO: needs direct nix store path to ddcutil - fix
            brightness-control-using-ddcutil
            # User-loadable themes from user directory
            user-themes
          ]);

        programs = {
          firefox = {
            enable = true;
            package = inputs.firefox.packages.${hostPlatform}.firefox-nightly-bin;
            profiles.main = {
              isDefault = true;
              search = {
                force = true;
                default = "Google";
                privateDefault = "Google";
              };
              bookmarks = [
                {
                  toolbar = true;
                  bookmarks = [
                    {
                      name = "Nix";
                      bookmarks = [
                        {
                          name = "Nix Manual";
                          url = "https://nix.dev/manual/nix/2.24/";
                        }
                        {
                          name = "Nixpkgs Manual";
                          url = "https://nixos.org/manual/nixpkgs/unstable/";
                        }
                        {
                          name = "NixOS Manual";
                          url = "https://nixos.org/manual/nixos/unstable/";
                        }
                        {
                          name = "NixOS Wiki";
                          url = "https://wiki.nixos.org/wiki/Main_Page";
                        }
                        {
                          name = "NixOS Wiki";
                          url = "https://wiki.nixos.org/wiki/Main_Page";
                        }
                      ];
                    }
                  ];
                }
              ];
              extensions = with pkgs.nur.repos.rycee.firefox-addons; [
                firefox-color
              ];
              settings = {
                "extensions.autoDisableScopes" = 0;
                "extensions.pocket.enabled" = false;
                "sidebar.verticalTabs" = true;
              };
            };
          };
        };

        # wayland.windowManager.hyprland = {
        #   enable = true;
        #   settings = { };
        #   plugins = { };
        # };
      };
    }
    .${kernel}
  ] ++ hmMods;

  # User-level Nix config
  nix = {
    package = lib.mkForce pkgs.nixVersions.latest;
    checkConfig = true;
  };

  # System-wide stylng
  stylix = {
    enable = true;
    base16Scheme = "${pkgs.base16-schemes}/share/themes/catppuccin-frappe.yaml";
    polarity = "dark";
    iconTheme = {
      enable = !pkgs.stdenv.isDarwin;
      package = pkgs.papirus-icon-theme;
      dark = "Papirus-Dark";
    };
    cursor = {
      package = pkgs.catppuccin-cursors.frappeBlue;
      name = "catppuccin-frappe-blue-cursors";
    };
    fonts = {
      serif = {
        package = pkgs.cantarell-fonts;
        name = "Cantarell";
      };
      sansSerif = {
        package = pkgs.cantarell-fonts;
        name = "Cantarell";
      };
      monospace = {
        package = pkgs.nerd-fonts.hack;
        name = "Hack Nerd Font Mono";
      };
      sizes = {
        applications = 11;
        desktop = 11;
        popups = 11;
        terminal = 9;
      };
    };
    image = ./wallpaper.jpeg;
    targets.nixvim.enable = false;
  };

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
    sessionPath = [
      "$HOME/.local/bin"
      "$HOME/.cargo/bin"
      "$HOME/go/bin"
    ];

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
      let
        ezaDefaultArgs = "-albghHistype -F --color=always --icons=always";
      in
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
    file =
      {
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
        ".local/bin" = {
          source = ./bin;
          recursive = true;
          executable = true;
        };
        "${config.xdg.configHome}/ptpython/config.py".source = ./config/ptpython.py;
        "${config.xdg.configHome}/bat/themes" = {
          source = "${inputs.catppuccin-bat}/themes";
          recursive = true;
        };
      }
      // (optionalAttrs (elem "basis" profiles) {
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
      # Cachix client
      cachix
      # cURL wrapper with niceties
      curlie
      # Duplicate file finder
      czkawka
      # DAta SELector - CLI for JSON, YAML, TOML, XML, and CSV
      dasel
      # Disk space usage analyzer (in Rust)
      du-dust
      # Disk Usage Analyzer
      dua
      # Disuk Usage/Free utility
      duf
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
      # Visual Git client with virtuale branches and first class conflicts
      # gitbutler
      # GitLab Command Line Interface
      glab
      # GRAPH VIsualiZer
      # Stream EDitor
      gnused
      # Tape ARchrive
      gnutar
      # Graphical Ping (ICMP)
      gping
      # API IDE
      hoppscotch
      # HTTP client
      httpie
      # Interactive JSON filter
      # TODO: figure out
      jnv
      # Additional useful utilities (a la coreutils)
      moreutils
      # Neovim Rust GUI
      (if pkgs.stdenv.isLinux then neovide else neovide.overrideAttrs { version = "0.12.2"; })
      # Hack Nerd Font
      nerd-fonts.hack
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
      # Universal file manager
      spacedrive
      # Music streaming
      # spotify
      # Fast SQL formatter
      sqruff
      # Aesthetic modern terminal file manager
      superfile
      # Code counter - enable after https://github.com/NixOS/nixpkgs/pull/268563
      tokei
      # SQL against CSV, LTSV, JSON, YAML, and TBLN
      trdsql
      # Rust-based Python package resolver & installed (faster pip)
      uv
      # Alternative to `watch`
      viddy
      # Wayland Clipboard
      wl-clipboard
      # YAML Query tool
      yq-go
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
        # font = {
        #   size = lib.mkDefault 12.0;
        #   normal.family = "Hack Nerd Font Mono";
        # };
        # Launch Zellij directly instead of going through a shell
        terminal.shell = {
          program = "${pkgs.zellij}/bin/zellij";
          # Attach to session called "main" if it exists, create one named that
          # if it doesn't
          # NOTE: this gets merged in with
          # device/home/lenovo-thinkpad-x1-carbon-gen10.nix#programs.alacritty.settings.shell.args
          # It comes last
          args = [
            "attach"
            "--create"
            "main"
          ];
        };
      };
    };
    # Amazon Web Services Command Line Interface
    awscli.enable = true;
    # More robust alternative to `cat`
    bat = {
      enable = true;
      config = {
        style = "full";
        theme = lib.mkForce "Catppuccin Frappe";
      };
      syntaxes.kdl = {
        src = inputs.sublime-kdl;
        file = "KDL.sublime-syntax";
      };
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
    fzf = {
      enable = true;
      enableZshIntegration = true;
    };
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
        options = {
          navigate = true;
          side-by-side = true;
        };
      };
      # difftastic = { enable = true; background = "dark"; };
      extraConfig = {
        commit.gpgsign = true;
        delta.features = lib.mkForce "catppuccin-frappe";
        diff.colorMoved = "default";
        fetch.prune = true;
        merge.conflictstyle = "diff3";
        rebase.pull = true;
        user.signingkey = meta.gpg.keys.personal;
        url."ssh://gitlab.gnome.org".insteadOf = "https://gitlab.gnome.org";
      };
      includes =
        [
          { path = "${inputs.catppuccin-delta}/catppuccin.gitconfig"; }
          {
            path = "${config.xdg.configHome}/git/personal";
            condition = "gitdir:~/.config/nixcfg/**";
          }
          {
            path = "${config.xdg.configHome}/git/personal";
            condition = "gitdir:~/${srcDir}/github.com/**";
          }
        ]
        ++ (lib.optionals (kernel == "darwin") [
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
    man = {
      enable = true;
      generateCaches = true;
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
        misc = {
          assume_yes = true;
          cleanup = true;
        };
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
    # High-performance code editor
    zed-editor = {
      enable = true;
      userSettings = {
        vim_mode = true;
        ui_font_size = 14;
        buffer_font_size = 12;
        theme = "Catppuccin Frappé";
      };
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
      initExtraBeforeCompInit =
        ''
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
      initExtra =
        ''
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
          src = (
            pkgs.fetchFromGitHub {
              owner = "kutsan";
              repo = "zsh-system-clipboard";
              rev = "5f66befd96529b28767fe8a239e9c6de6d57cdc4";
              hash = "sha256-t4xPKd9BvrH4cyq8rN/IVGcm13OJNutdZ4e+FdGbPIo=";
            }
          );
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
    # Fast directory jumper
    zoxide = {
      enable = true;
      enableNushellIntegration = true;
    };
  };
}
