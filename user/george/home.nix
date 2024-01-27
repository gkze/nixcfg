{ config, lib, pkgs, inputs, hostPlatform, hmMods ? [ ], ... }:
let
  inherit (builtins) elemAt readFile split;
  inherit (lib) optionalString removeSuffix;
  inherit (lib.lists) flatten;

  # Grab the OS kernel part of the hostPlatform tuple
  kernel = elemAt (split "-" hostPlatform) 2;

  # Source code directory
  srcDir = { darwin = "Development"; linux = "src"; }.${kernel};
in
{
  # Home Manager modules go here
  imports = [
    inputs.nixvim.homeManagerModules.nixvim
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
        services = {
          # Activate GPG agent on Linux
          gpg-agent = { enable = true; pinentryFlavor = "gnome3"; };

          # Nix shell direnv background daemon
          lorri.enable = true;

          # File synchronization
          # TODO: Factor out into Basis profile
          syncthing.enable = true;
        };

        # These are marked as unsupported on darwin
        home.packages = with pkgs; [
          # Database GUI
          beekeeper-studio
          # Web browser
          brave
          # Display Data Channel UTILity
          ddcutil
          # Matrix client
          element-desktop
          # Web browser
          firefox
          # Brightness control for all detected monitors
          # Currently managed manually
          # TODO: fix
          gnomeExtensions.brightness-control-using-ddcutil
          # Productivity suite
          libreoffice
          # For Basis
          # TODO: factor out into Basis profile
          networkmanager-openvpn
          # TODO: TBD if works on macOS
          signal-desktop
        ]
        ++ (with pkgs.gnome; [
          # Additional GNOME settings tool
          gnome-tweaks
          # Additional GNOME settings editing tool
          # https://wiki.gnome.org/Apps/DconfEditor
          dconf-editor
        ]);
      };
    }.${kernel}
  ] ++ hmMods;

  # Automatically discover installed fonts
  fonts.fontconfig.enable = true;

  # TODO: figure out and make NixOS-only (for now?)
  # wayland.windowManager.hyprland.enable = true;

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
    sessionPath = [ "$HOME/.local/bin" "$HOME/go/bin" ];

    # Shell-agnostic session environment variables
    # These only get applied on login
    sessionVariables = {
      # Set Neovim as the default editor
      EDITOR = "nvim";
      # Set Bat with man syntax highlighting as the default man pager
      MANPAGER = "sh -c 'col -bx | bat -plman'";
      # To fix formatting for man pages
      # https://github.com/sharkdp/bat#man
      MANROFFOPT = "-c";
      # Set Bat as the Nix pager
      NIX_PAGER = "bat -p";
      # Set Bat as the default pager
      PAGER = "bat -p";
    };

    # Universal cross-shell aliases
    shellAliases =
      let ezaDefaultArgs = "-FlabghHistype --color=always --icons=always"; in
      # Add aliases here
      {
        cr = "clear && reset";
        ezap = "eza ${ezaDefaultArgs}";
        ezat = "eza ${ezaDefaultArgs} --tree";
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
      "${config.xdg.configHome}/npmrc".text = ''
        prefix = "''${HOME}/.local"
      '';
      # Same as above for pip
      "${config.xdg.configHome}/pip/pip.conf".text = ''
        [install]
        user = true
      '';
      "${config.xdg.configHome}/git/personal".text = ''
        [user]
        	name = gkze
        	email = george.kontridze@gmail.com
      '';
      "${config.xdg.configHome}/git/basis".text = ''
        [user]
        	name = george
        	email = george@usebasis.co
      '';
      ".local/bin" = { source = ./bin; recursive = true; executable = true; };
    };

    # Packages that should be installed to the user profile. These are just
    # installed. Programs section below both installs and configures software,
    # and is the preferred method.
    packages = with pkgs; [
      # Password manager
      # TODO: factor out into Basis profile
      _1password-gui
      # Color theme
      catppuccin
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
      # GitLab Command Line Interface
      glab
      # Stream EDitor
      gnused
      # Tape ARchrive
      gnutar
      # Graphical Ping (ICMP)
      gping
      # HTTP client
      httpie
      # Additional useful utilities (a la coreutils)
      moreutils
      # Nerd Fonts
      # - https://www.nerdfonts.com/
      # - https://github.com/ryanoasis/nerd-fonts
      # Only install Hack Nerd Font, since the entire package / font repository
      # is quite large
      (nerdfonts.override { fonts = [ "Hack" ]; })
      # For running one-off npx stuff
      nodejs_latest
      # Knowledge management
      # TODO: factor out into Basis profile
      obsidian
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
      # Code counter - enable after https://github.com/NixOS/nixpkgs/pull/268563
      tokei
      # Alternative to `watch`
      viddy
      # Wayland Clipboard
      wl-clipboard
      # Git branch maintenance
      # TODO: upstream to Nixpkgs
      (stdenv.mkDerivation {
        name = "git-trim";
        src = pkgs.fetchFromGitHub {
          owner = "jasonmccreary";
          repo = "git-trim";
          rev = "5f05032011948c306661687f2abdc33e738ec7b4";
          hash = "sha256-fukz/9hnJCnKyrpStwycTdHYJYJDMcCD2YDJ9VLN2hM=";
        };
        installPhase = ''
          mkdir -p $out/bin
          cp git-trim $out/bin
          chmod +x $out/bin/git-trim
        '';
      })
      (
        let
          binVersion = "0.17.2";
          hostPlatformElems = split "-" hostPlatform;
          nixArch = elemAt hostPlatformElems 0;
          arch = { x86_64 = "amd64"; aarch64 = "arm64"; }.${nixArch};
          kernel = elemAt hostPlatformElems 2;
          binName = "bin_${binVersion}_${kernel}_${arch}";
        in
        stdenv.mkDerivation {
          name = "bin";
          src = fetchurl {
            url = "https://github.com/marcosnils/bin/releases/download/v${binVersion}/${binName}";
            hash = {
              aarch64-darwin = "sha256-/KgRUpF4bJCfbwp5V+R0uPKfXwH+KluThLYN7sBdpbk=";
              x86_64-linux = "sha256-C3h+35qTRkSWf6dRyrgl082FFXtgNgaLgicDbCFUOrY=";
            }.${hostPlatform};
          };
          dontUnpack = true;
          installPhase = ''
            mkdir -p $out/bin
            cp $src $out/bin/bin
            chmod +x $out/bin/bin
          '';
          # TODO: get working
          nativeBuildInputs = [ installShellFiles ];
          postInstall = ''
            installShellCompletion --zsh <($out/bin/bin completion zsh)
          '';
        }
      )
    ];
  };

  # Install and configure user-level software
  programs = {
    # Let Home Manager install and manage itself
    home-manager.enable = true;
    # Terminal emulator
    alacritty = {
      enable = true;
      settings = {
        # TODO: re-enable once https://github.com/NixOS/nixpkgs/issues/279707
        # is resolved
        # import = [ "${pkgs.alacritty-theme}/catppuccin_frappe.yaml" ];
        import = [
          (pkgs.stdenv.mkDerivation {
            name = "catppuccin-alacritty-frappe";
            src = pkgs.fetchurl {
              url = "https://raw.githubusercontent.com/catppuccin/alacritty/f2da554ee63690712274971dd9ce0217895f5ee0/catppuccin-frappe.toml";
              hash = "sha256-Rhr5XExaY0YF2t+PVwxBRIXQ58TH1+kMue7wkKNaSJI=";
            };
            dontUnpack = true;
            installPhase = ''
              cp $src $out
            '';
          })
        ];
        # # Base16 Gruvbox Dark Soft 256
        # # https://github.com/aarowill/base16-alacritty/blob/master/colors/base16-gruvbox-dark-soft-256.yml
        # colors = {
        #   # Default colors
        #   primary = { background = "0x32302f"; foreground = "0xd5c4a1"; };
        #   # Colors the cursor will use if `custom_cursor_colors` is true
        #   cursor = { text = "0x32302f"; cursor = "0xd5c4a1"; };
        #   # Normal colors
        #   normal = {
        #     black = "0x32302f";
        #     red = "0xfb4934";
        #     green = "0xb8bb26";
        #     yellow = "0xfabd2f";
        #     blue = "0x83a598";
        #     magenta = "0xd3869b";
        #     cyan = "0x8ec07c";
        #     white = "0xd5c4a1";
        #   };
        #   # Bright colors
        #   bright = {
        #     black = "0x665c54";
        #     red = "0xfb4934";
        #     green = "0xb8bb26";
        #     yellow = "0xfabd2f";
        #     blue = "0x83a598";
        #     magenta = "0xd3869b";
        #     cyan = "0x8ec07c";
        #     white = "0xfbf1c7";
        #   };
        #   indexed_colors = [
        #     { index = 16; color = "0xfe8019"; }
        #     { index = 17; color = "0xd65d0e"; }
        #     { index = 18; color = "0x3c3836"; }
        #     { index = 19; color = "0x504945"; }
        #     { index = 20; color = "0xbdae93"; }
        #     { index = 21; color = "0xebdbb2"; }
        #   ];
        # };
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
    # Zsh
    zsh = {
      enable = true;
      # Type directory names directly without `cd` to `cd` into them
      autocd = true;
      # Paths to index for the above
      cdpath = [ srcDir ];
      # $ZDOTDIR (https://zsh.sourceforge.io/Doc/Release/Files.html#Files)
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
      # Any additional manual confiuration goes here
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
            docker completion zsh > $out/_docker;
          '';
          file = "_docker";
        }
      ];
    };
    vscode.enable = true;
    # Modern shell that focuses on structured data
    # TODO: tune
    nushell.enable = true;
    # Shell prompt
    starship = {
      enable = true;
      enableZshIntegration = true;
      enableNushellIntegration = true;
      settings = { add_newline = false; line_break.disabled = true; };
    };
    # Terminal multiplexer / workspace manager
    zellij = {
      enable = true;
      settings = {
        keybinds.normal."bind \"Alt s\"".Clear = { };
        session_serialization = false;
        simplified_ui = true;
        theme = "catppuccin-frappe";
      };
    };
    # Terminal file manager
    yazi = {
      enable = true;
      enableZshIntegration = true;
      settings.manager.sort_by = "alphabetical";
    };
    # Neovim configured with Nix - NEEDS TUNING
    nixvim = {
      enable = true;
      enableMan = true;
      colorschemes.catppuccin = { enable = true; flavour = "frappe"; };
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
          layout = [
            { type = "padding"; val = 2; opts.position = "center"; }
            {
              type = "text";
              val = [
                "  ███╗   ██╗██╗██╗  ██╗██╗   ██╗██╗███╗   ███╗  "
                "  ████╗  ██║██║╚██╗██╔╝██║   ██║██║████╗ ████║  "
                "  ██╔██╗ ██║██║ ╚███╔╝ ██║   ██║██║██╔████╔██║  "
                "  ██║╚██╗██║██║ ██╔██╗ ╚██╗ ██╔╝██║██║╚██╔╝██║  "
                "  ██║ ╚████║██║██╔╝ ██╗ ╚████╔╝ ██║██║ ╚═╝ ██║  "
                "  ╚═╝  ╚═══╝╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚═╝     ╚═╝  "
              ];
              opts = { position = "center"; hl = "Type"; };
            }
            { type = "padding"; val = 2; opts.position = "center"; }
            {
              type = "group";
              val = [
                {
                  type = "button";
                  val = " New file";
                  on_press.__raw = "function() vim.cmd[[ene]] end";
                  opts = {
                    align_shortcut = "right";
                    cursor = 3;
                    hl_shortcut = "Keyword";
                    keymap = [ "n" "e" ":ene<CR>" { noremap = true; nowait = true; silent = true; } ];
                    position = "center";
                    shortcut = "e";
                    width = 50;
                  };
                }
                # TODO: get working
                # {
                #   type = "button";
                #   val = "󰈞 Find file";
                #   on_press.__raw = "function() require(\"telescope.builtin\").find_files end";
                #   opts = {
                #     align_shortcut = "right";
                #     cursor = 3;
                #     hl_shortcut = "Keyword";
                #     keymap = [ "n" "f" ":Telescope find_files<CR>" { noremap = true; nowait = true; silent = true; } ];
                #     position = "center";
                #     shortcut = "f";
                #     width = 50;
                #   };
                # }
                # {
                #   type = "button";
                #   val = "󰈞 Find string(s)";
                #   on_press.__raw = "function() require(\"telescope.builtin\").live_grep end";
                #   opts = {
                #     align_shortcut = "right";
                #     cursor = 3;
                #     hl_shortcut = "Keyword";
                #     keymap = [ "n" "g" ":Telescope live_grep<CR>" { noremap = true; nowait = true; silent = true; } ];
                #     position = "center";
                #     shortcut = "g";
                #     width = 50;
                #   };
                # }
                {
                  type = "button";
                  val = " Quit Neovim";
                  on_press.__raw = "function() vim.cmd[[qa]] end";
                  opts = {
                    align_shortcut = "right";
                    cursor = 3;
                    hl_shortcut = "Keyword";
                    keymap = [ "n" "q" ":qa<CR>" { noremap = true; nowait = true; silent = true; } ];
                    position = "center";
                    shortcut = "q";
                    width = 50;
                  };
                }
              ];
            }
            { type = "padding"; val = 2; opts.position = "center"; }
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
          diagnostics = "nvim_lsp";
          enforceRegularTabs = false;
          offsets = [{
            filetype = "neo-tree";
            text = "Neo-tree";
            separator = true;
            textAlign = "left";
          }];
        };
        #Git information
        gitsigns = { enable = true; currentLineBlame = true; currentLineBlameOpts.delay = 300; };
        # Language Server Protocol client
        lsp = {
          enable = true;
          keymaps.lspBuf = {
            "gd" = "definition";
            "gD" = "references";
            "gt" = "type_definition";
            "gi" = "implementation";
            "K" = "hover";
          };
          servers = {
            # Nix (nil with nixpkgs-fmt)
            # TODO: determine if nil or nixd is better
            # nixd = {
            #   enable = true;
            #   settings.formatting.command = "${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt";
            # };
            nil_ls = {
              enable = true;
              settings.formatting.command = [ "${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt" ];
            };
            bashls.enable = true;
            cssls.enable = true;
            dockerls.enable = true;
            efm.enable = true;
            eslint.enable = true;
            gopls.enable = true;
            html.enable = true;
            jsonls.enable = true;
            pyright.enable = true;
            ruff-lsp.enable = false;
            tsserver.enable = true;
            yamlls.enable = true;
          };
        };
        # Symbol navigation popup
        navbuddy = { enable = true; lsp.autoAttach = true; };
        # Neovim git interface
        neogit = { enable = true; integrations.diffview = true; };
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
          window.mappings = { "<A-{>" = "prev_source"; "<A-}>" = "next_source"; };
        };
        # LSP completion
        nvim-cmp = {
          enable = true;
          snippet.expand = "luasnip";
          sources = [
            { name = "nvim_lsp"; }
            { name = "luasnip"; }
            { name = "path"; }
            { name = "buffer"; }
          ];
          # mappingPresets = [ "insert" "cmdline" ];
          mapping =
            let
              selectNextItemFn = ''
                function(fallback)
                  local luasnip = require('luasnip')

                  if cmp.visible() then
                    cmp.select_next_item()
                  elseif luasnip.expandable() then
                    luasnip.expand()
                  elseif luasnip.expand_or_jumpable() then
                    luasnip.expand_or_jump()
                  else
                    fallback()
                  end
                end
              '';
              selectPrevItemFn = ''
                function(callback)
                  if cmp.visible then
                    cmp.select_prev_item()
                  else
                    fallback()
                  end
                end
              '';
            in
            {
              "<CR>" = "cmp.mapping.confirm({ select = true })";
              "<Tab>" = { modes = [ "i" "s" ]; action = selectNextItemFn; };
              "<S-Tab>" = { modes = [ "i" "s" ]; action = selectPrevItemFn; };
              # Yes for some reason it's backward
              "<Up>" = { modes = [ "s" ]; action = selectPrevItemFn; };
              "<Down>" = { modes = [ "s" ]; action = selectNextItemFn; };
            };
        };
        # Tree-sitter text objects
        # TODO: figure out
        treesitter-textobjects = {
          enable = true;
          move = {
            enable = true;
            gotoNextStart = {
              "]]" = "@class.outer";
              "]a" = "@attribute.outer";
              "]k" = "@keyword";
              "]m" = "@function.outer";
              "]p" = "@property";
            };
            gotoNextEnd = {
              "]A" = "@attribute.outer";
              "]K" = "@keyword";
              "]M" = "@function.outer";
              "]P" = "@property";
              "][" = "@class.outer";
            };
            gotoPreviousStart = {
              "[[" = "@class.outer";
              "[a" = "@attribute.outer";
              "[k" = "@keyword";
              "[m" = "@function.outer";
              "[p" = "@property";
            };
            gotoPreviousEnd = {
              "[A" = "@attribute.outer";
              "[K" = "@keyword";
              "[M" = "@function.outer";
              "[]" = "@class.outer";
              "[p" = "@property";
            };
          };
          select = {
            enable = true;
            lookahead = true;
            keymaps = {
              "ac" = "@class.outer";
              "af" = "@function.outer";
              "as" = "@scope";
              "ic" = "@class.inner";
              "if" = "@function.inner";
              "p" = "@property";
            };
          };
        };
        # File / AST breadcrumbs
        barbecue.enable = true;
        # Code commenting
        comment-nvim.enable = true;
        # Debug Adapter Protocol
        dap.enable = true;
        # Highlight other occurrences of word under cursor
        illuminate.enable = true;
        # Indentation guide
        indent-blankline.enable = true;
        # Status line (bottom)
        lualine.enable = true;
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
        # Enable Nix language support
        nix.enable = true;
        # File finder (popup)
        telescope.enable = true;
        # Enable working with TODO: code comments
        todo-comments.enable = true;
        # Built-in terminal
        toggleterm = { enable = true; size = 10; };
        # Parser generator & incremental parsing toolkit
        treesitter = { enable = true; incrementalSelection.enable = true; };
        # Diagnostics, etc. 
        trouble.enable = true;
      };
      extraPlugins = flatten (with pkgs; [
        # TODO: add comments for each plugin
        (with vimPlugins; [
          aerial-nvim
          bufdelete-nvim
          git-conflict-nvim
          nvim-surround
          nvim-treesitter-textsubjects
          vim-jinja
        ])
        # TODO: upstream to Nixpkgs
        (stdenv.mkDerivation {
          name = "mini-align";
          src = fetchFromGitHub {
            owner = "echasnovski";
            repo = "mini.align";
            rev = "708c0265b1513a00c83c181bff3d237741031cd1";
            hash = "sha256-tDf6zUoSU9f1PJ8Di6+iV8MCTaXPEEgbQZlGRFa9Dss=";
          };
          installPhase = "cp -r $src $out";
        })
        # TODO: upstream to Nixpkgs
        (stdenv.mkDerivation {
          name = "vim-bundle-mako";
          src = fetchFromGitHub {
            owner = "sophacles";
            repo = "vim-bundle-mako";
            rev = "09d2a93b1a853972ccfca44495d597c717789232";
            hash = "sha256-q5PgPAKjyjLUuKvK6S1m8huQ1G7SoFvq9bmQmlMoS1g=";
          };
          installPhase = "cp -r $src $out";
        })
      ]);
      extraConfigLua = ''
        require("aerial").setup({
          autojump = true,
          filter_kind = false,
          open_automatic = true
        })
        require("git-conflict").setup()
        require("mini.align").setup()
        require("nvim-surround").setup()
        require('nvim-treesitter.configs').setup {
            textsubjects = {
                enable = true,
                prev_selection = ',',
                keymaps = {
                    ['.'] = 'textsubjects-smart',
                    [';'] = 'textsubjects-container-outer',
                    ['i;'] = 'textsubjects-container-inner',
                    ['i;'] = 'textsubjects-container-inner',
                },
            },
        }
      '';
      keymaps = [
        { key = ";"; action = ":"; }
        { key = "<A-(>"; action = ":BufferLineMovePrev<CR>"; }
        { key = "<A-)>"; action = ":BufferLineMoveNext<CR>"; }
        { key = "<A-W>"; action = ":wall<CR>"; }
        { key = "<A-w>"; action = ":write<CR>"; }
        { key = "<A-w>"; action = ":write<CR>"; }
        { key = "<A-x>"; action = ":Bdelete<CR>"; }
        { key = "<A-{>"; action = ":BufferLineCyclePrev<CR>"; }
        { key = "<A-}>"; action = ":BufferLineCycleNext<CR>"; }
        { key = "<C-l>"; action = ":set invlist<CR>"; }
        { key = "<S-f>"; action = ":ToggleTerm direction=float<CR>"; } # TODO: figure out how to resize
        { key = "<S-s>"; action = ":sort<CR>"; }
        { key = "<S-t>"; action = ":ToggleTerm<CR>"; }
        { key = "<leader>F"; action = ":Telescope find_files hidden=true<CR>"; }
        { key = "<leader>a"; action = ":AerialToggle<CR>"; }
        { key = "<leader>b"; action = ":Neotree toggle buffers<CR>"; }
        { key = "<leader>c"; action = ":nohlsearch<CR>"; }
        { key = "<leader>de"; action = ":TodoTelescope<CR>"; }
        { key = "<leader>dl"; action = ":TodoLocList<CR>"; }
        { key = "<leader>dr"; action = ":TodoTrouble<CR>"; }
        { key = "<leader>f"; action = ":Telescope find_files<CR>"; }
        { key = "<leader>g"; action = ":Telescope live_grep<CR>"; }
        { key = "<leader>h"; action = ":wincmd h<CR>"; }
        { key = "<leader>j"; action = ":wincmd j<CR>"; }
        { key = "<leader>k"; action = ":wincmd k<CR>"; }
        { key = "<leader>l"; action = ":wincmd l<CR>"; }
        { key = "<leader>m"; action = ":Telescope keymaps<CR>"; }
        { key = "<leader>n"; action = ":Neotree focus<CR>"; }
        { key = "<leader>p"; action = ":TroubleToggle<CR>"; }
        { key = "<leader>r"; action = ":Neotree reveal<CR>"; }
        { key = "<leader>s"; action = ":Navbuddy<CR>"; }
        { key = "<leader>t"; action = ":Neotree toggle filesystem<CR>"; }
        { key = "<leader>v"; action = ":Neotree toggle git_status<CR>"; }
        { key = "<leader>x"; action = ":Neogit<CR>"; }
      ];
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
    # Version control
    git = {
      enable = true;
      aliases = {
        branches = "branch --sort=-committerdate --format='%(committerdate)\t::  %(refname:short)'";
        praise = "blame";
      };
      delta = { enable = true; options = { side-by-side = true; }; };
      # difftastic = { enable = true; background = "dark"; };
      extraConfig = {
        commit.gpgsign = true;
        fetch.prune = true;
        merge.conflictstyle = "diff3";
        rebase.pull = true;
        user.signingkey = "9578FF9AB0BDE622307E7E833A7266FAC0D2F08D";
      };
      includes = [
        {
          path = "${config.xdg.configHome}/git/personal";
          condition = "gitdir:~/.config/nixcfg/**";
        }
        {
          path = "${config.xdg.configHome}/git/basis";
          condition = "gitdir:~/${srcDir}/git.usebasis.co/**";
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
      keyConfig = ''
        // bit for modifiers
        // bits: 0  None 
        // bits: 1  SHIFT
        // bits: 2  CONTROL
        //
        // Note:
        // If the default key layout is lower case,
        // and you want to use `Shift + q` to trigger the exit event,
        // the setting should like this `exit: Some(( code: Char('Q'), modifiers: ( bits: 1,),)),`
        // The Char should be upper case, and the shift modified bit should be set to 1.
        //
        // Note:
        // find `KeysList` type in src/keys/key_list.rs for all possible keys.
        // every key not overwritten via the config file will use the default specified there
        (
            open_help: Some(( code: F(1), modifiers: ( bits: 0,),)),

            move_left: Some(( code: Char('h'), modifiers: ( bits: 0,),)),
            move_right: Some(( code: Char('l'), modifiers: ( bits: 0,),)),
            move_up: Some(( code: Char('k'), modifiers: ( bits: 0,),)),
            move_down: Some(( code: Char('j'), modifiers: ( bits: 0,),)),
    
            popup_up: Some(( code: Char('p'), modifiers: ( bits: 2,),)),
            popup_down: Some(( code: Char('n'), modifiers: ( bits: 2,),)),
            page_up: Some(( code: Char('b'), modifiers: ( bits: 2,),)),
            page_down: Some(( code: Char('f'), modifiers: ( bits: 2,),)),
            home: Some(( code: Char('g'), modifiers: ( bits: 0,),)),
            end: Some(( code: Char('G'), modifiers: ( bits: 1,),)),
            shift_up: Some(( code: Char('K'), modifiers: ( bits: 1,),)),
            shift_down: Some(( code: Char('J'), modifiers: ( bits: 1,),)),

            edit_file: Some(( code: Char('I'), modifiers: ( bits: 1,),)),

            status_reset_item: Some(( code: Char('U'), modifiers: ( bits: 1,),)),

            diff_reset_lines: Some(( code: Char('u'), modifiers: ( bits: 0,),)),
            diff_stage_lines: Some(( code: Char('s'), modifiers: ( bits: 0,),)),

            stashing_save: Some(( code: Char('w'), modifiers: ( bits: 0,),)),
            stashing_toggle_index: Some(( code: Char('m'), modifiers: ( bits: 0,),)),

            stash_open: Some(( code: Char('l'), modifiers: ( bits: 0,),)),

            abort_merge: Some(( code: Char('M'), modifiers: ( bits: 1,),)),
        )
      '';
    };
    # GitHub CLI
    gh.enable = true;
    # Manual page interface
    man = { enable = true; generateCaches = true; };
    # FuZzy Finder - finds items in lists. Current integrations / use cases:
    # - Zsh history search
    # - Neovim file picking
    fzf = { enable = true; enableZshIntegration = true; };
    # More robust alternative to `cat`
    bat = {
      enable = true;
      themes.catppuccin-frappe = {
        src = pkgs.fetchFromGitHub {
          owner = "catppuccin";
          repo = "bat";
          rev = "ba4d16880d63e656acced2b7d4e034e4a93f74b1";
          hash = "sha256-6WVKQErGdaqb++oaXnY3i6/GuH2FhTgK0v4TN4Y0Wbw=";
        };
        file = "./Catppuccin-frappe.tmTheme";
      };
      config.style = "full";
    };
    # `ls` alternative
    eza.enable = true;
    # Executes commands when changing to a directory with an `.envrc` in it
    # nix-direnv is a faster and persistent implementaiton of direnv's use_nix
    # and use_flake
    direnv = {
      enable = true;
      nix-direnv.enable = true;
      enableZshIntegration = true;
    };
    # `cd` replacement that ranks frequently / recently used
    # directories for easier shorthand access
    zoxide = { enable = true; enableNushellIntegration = true; };
    # JSON Querier
    jq.enable = true;
    # Nixpkgs file database
    nix-index.enable = true;
    # Alternative to `grep`
    ripgrep.enable = true;
    # Right now this breaks with sandbox-exec error
    # https://github.com/NixOS/nix/issues/4119
    # Java (to satisfy Visual Studio Code Java extension - possibly factor out)
    java.enable = true;
    # Go
    # TODO: debug
    go.enable = true;
    # # Amazon Web Services Command Line Interface
    awscli = {
      enable = true;
      settings = {
        default = { region = "us-east-1"; output = "json"; };
        # TODO: factor out into Basis profile
        "profile development" = {
          sso_session = "basis";
          sso_account_id = 820061307359;
          sso_role_name = "PowerUserAccess";
        };
        "profile staging" = {
          sso_session = "basis";
          sso_account_id = 523331955727;
          sso_role_name = "PowerUserAccess";
        };
        "profile production" = {
          sso_session = "basis";
          sso_account_id = 432644110438;
          sso_role_name = "PowerUserAccess";
        };
        "sso-session basis" = {
          sso_start_url = "https://d-90679b66bf.awsapps.com/start";
          sso_region = "us-east-1";
          sso_registration_scopes = "sso:account:access";
        };
      };
    };
    # GnuPG
    gpg = {
      enable = true;
      homedir = "${config.xdg.dataHome}/gnupg";
      settings = {
        auto-key-retrieve = true;
        default-key = "9578FF9AB0BDE622307E7E833A7266FAC0D2F08D";
      };
    };
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
        "rocinante" = {
          hostname = "10.0.0.241";
          user = "george";
          identityFile = "~/.ssh/personal.pem";
        };
      };
    };
    # Unified software upgrader
    topgrade = {
      enable = true;
      settings = {
        misc = { assume_yes = true; cleanup = true; };
        git.pull_only_repos = [ srcDir ];
      };
    };
  };
}
