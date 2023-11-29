{ config, lib, pkgs, inputs, hostPlatform, hmMods ? [ ], ... }:
let
  inherit (builtins) elemAt readFile split;
  inherit (lib) optionals optionalString removeSuffix;
  inherit (lib.strings) concatStringsSep;

  # Grab the OS kernel part of the hostPlatform tuple
  kernel = elemAt (split "-" hostPlatform) 2;

  # Source code directory
  srcDir = { darwin = "Development"; linux = "src"; }.${kernel};
in
{
  # Home Manager modules go here
  imports =
    [ inputs.nixvim.homeManagerModules.nixvim ]
    # TODO make sure this is well settled
    ++ (optionals (kernel == "linux") [ ./dconf.nix ])
    ++ hmMods;

  # Automatically discover installed fonts
  fonts.fontconfig.enable = true;

  services = {
    # Activate GPG agent on Linux
    gpg-agent.enable = kernel == "linux";

    # File synchronization
    # TODO Factor out into Basis profile
    syncthing.enable = true;
  };

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

    # Shell-agnostic session environment variables that are always set 
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
      # Set $PATH. Priority is:
      # - User-local executables in $HOME/.local/bin
      PATH = concatStringsSep ":" [ ''''${HOME}/.local/bin'' ''''${PATH}'' ];
    };

    # Universal cross-shell aliases
    shellAliases =
      let
        ezaDefaultArgs = "-FlabghHistype --color=always --icons=always";
      in
      # Add aliases here
      {
        cr = "clear && reset";
        ezap = "eza ${ezaDefaultArgs}";
        ezat = "eza ${ezaDefaultArgs} --tree";
        ne = "cd ~/.config/nixcfg && nvim";
        nv = "nvim";
        zac = "zellij action clear";
        zj = "zellij";
        zq = "zellij kill-all-sessions --yes && zellij delete-all-sessions --force --yes";
      };

    file =
      # Wire up symlinks to everything under $ROOT/user/$USER/home from $HOME
      # That way no additional code needs to be written
      # Not in use right now, should probably not be needed as most software
      # should be configured via `programs` attribute
      # mapAttrs
      #   (n: v: {
      #     source = ./home/${n};
      #     recursive = if v == "directory" then true else false;
      #   })
      #   (readDir ./home)
      # //
      # # Additional manual files for various reasons
      {
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
        ".local/bin" = {
          source = ./home/.local/bin;
          recursive = true;
          executable = true;
        };
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
      };

    # Packages that should be installed to the user profile. These are just
    # installed. Programs section below both installs and configures software,
    # and is the preferred method.
    packages =
      with pkgs; [
        # Web browser
        brave
        # Duplicate file finder
        czkawka
        # Display Data Channel UTILity
        ddcutil
        # Matrix client
        # TODO factor out into Basis profile
        # TODO TBD if works on macOS
        element-desktop
        # Envchain is a utility that loads environment variables from the system
        # keychain
        envchain
        # Alternative to `find`
        fd
        # GitLab Command Line Interface
        glab
        # Brightness control for all detected monitors
        # Currently managed manually
        # TODO fix
        # gnomeExtensions.brightness-control-using-ddcutil
        # File transfer over LAN
        localsend
        # Additional useful utilities (a la coreutils)
        moreutils
        # Nerd Fonts
        # - https://www.nerdfonts.com/
        # - https://github.com/ryanoasis/nerd-fonts
        # Only install Hack Nerd Font, since the entire package / font repository
        # is quite large
        (nerdfonts.override { fonts = [ "Hack" ]; })
        # Knowledge management
        obsidian
        # For Basis
        # TODO factor out into Basis profile
        networkmanager-openvpn
        # Alternative to `sed`
        sd
        # TODO TBD if works on macOS
        slack
        # TODO TBD if works on macOS
        signal-desktop-beta
        # Code counter - enable after https://github.com/NixOS/nixpkgs/pull/268563
        # tokei 
        # Alternative to `watch`
        viddy
        # File transfer over LAN
        warp
        # Clipboard utility
        xclip
      ]
      ++ (with inputs; [
        # Nix software management GUI
        nix-software-center.packages.${hostPlatform}.default
        # Nix configuration editor GUI
        nixos-conf-editor.packages.${hostPlatform}.default
      ])
      ++ (with pkgs.gnome; [
        # Additional GNOME settings tool
        # TODO factor out into nixos-only home manager
        gnome-tweaks
        # Additional GNOME settings editing tool
        # https://wiki.gnome.org/Apps/DconfEditor
        dconf-editor
      ])
    ;
  };

  # Install and configure user-level software
  programs = {
    # Let Home Manager install and manage itself
    home-manager.enable = true;
    # Terminal emulator
    alacritty = {
      enable = true;
      settings = {
        # Base16 Gruvbox Dark Soft 256
        # https://github.com/aarowill/base16-alacritty/blob/master/colors/base16-gruvbox-dark-soft-256.yml
        colors = {
          # Default colors
          primary = { background = "0x32302f"; foreground = "0xd5c4a1"; };
          # Colors the cursor will use if `custom_cursor_colors` is true
          cursor = { text = "0x32302f"; cursor = "0xd5c4a1"; };
          # Normal colors
          normal = {
            black = "0x32302f";
            red = "0xfb4934";
            green = "0xb8bb26";
            yellow = "0xfabd2f";
            blue = "0x83a598";
            magenta = "0xd3869b";
            cyan = "0x8ec07c";
            white = "0xd5c4a1";
          };
          # Bright colors
          bright = {
            black = "0x665c54";
            red = "0xfb4934";
            green = "0xb8bb26";
            yellow = "0xfabd2f";
            blue = "0x83a598";
            magenta = "0xd3869b";
            cyan = "0x8ec07c";
            white = "0xfbf1c7";
          };
          indexed_colors = [
            { index = 16; color = "0xfe8019"; }
            { index = 17; color = "0xd65d0e"; }
            { index = 18; color = "0x3c3836"; }
            { index = 19; color = "0x504945"; }
            { index = 20; color = "0xbdae93"; }
            { index = 21; color = "0xebdbb2"; }
          ];
        };
        font = { size = lib.mkDefault 12.0; normal.family = "Hack Nerd Font Mono"; };
        # Launch Zellij directly instead of going through a shell
        shell = {
          program = "${pkgs.zellij}/bin/zellij";
          # Attach to session called "main" if it exists, create one named that
          # if it doesn't
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
      # Placed in $ZDOTDIR/.zshrc
      initExtra = ''
        # vi: ft=zsh
        # shellcheck disable=all

        # Zsh builtins help
        unalias &>/dev/null run-help && autoload run-help

        # Load completion system
        zmodload zsh/complist
        autoload -Uz +X compinit bashcompinit select-word-style
        select-word-style bash

        # Tune the completion system a bit
        zstyle ':completion:*' menu select
        bindkey -M menuselect '^[[Z' reverse-menu-complete

        # Keep elements of {,MAN}PATH unique
        typeset -U PATH MANPATH
      '' + optionalString (kernel == "darwin") ''
        # Enable Homebrew on macOS

        # Set Homebrew prefix for M1 Mac
        HOMEBREW_PREFIX="/opt/homebrew"

        # For the rare case we're running on an Intel Mac
        if [[ "$(uname -m)" != "arm64" ]]; then
          HOMEBREW_PREFIX="/usr/local"
        fi

        # Just the essentials
        export PATH="''${HOMEBREW_PREFIX}/bin:''${PATH}"
        export MANPATH="''${HOMEBREW_PREFIX}/share/man:''${MANPATH}"
      '';
      # Placed in $ZDOTDIR/.zshrc before compinit
      initExtraBeforeCompInit = optionalString (kernel == "darwin") ''
        # Homebrew Zsh completion
        fpath+="/opt/homebrew/share/zsh/site-functions"
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
          src = ./home/.config/zsh/plugins;
        }
        # Use FZF for searching through history
        {
          name = "zsh-fzf-history-search";
          src = "${pkgs.zsh-fzf-history-search}/share/zsh-fzf-history-search";
        }
        # Yank selections into system clipboard
        {
          name = "zsh-system-clipboard";
          src = (pkgs.fetchFromGitHub {
            owner = "kutsan";
            repo = "zsh-system-clipboard";
            rev = "5f66befd96529b28767fe8a239e9c6de6d57cdc4";
            hash = "sha256-t4xPKd9BvrH4cyq8rN/IVGcm13OJNutdZ4e+FdGbPIo=";
          });
        }
        # docker(d) CLI completion
        {
          name = "docker";
          src = "${pkgs.docker}/share/zsh/site-functions";
          file = "_docker";
        }
      ];
    };
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
        keybinds.scroll."bind \"c\"".Clear = { };
        pane_frames = false;
        session_serialization = false;
        theme = "gruvbox-dark";
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
      # https://github.com/nix-community/nixvim/issues/754
      # https://github.com/nix-community/nixvim/pull/751
      # https://github.com/NixOS/nixpkgs/pull/269942
      enableMan = false;
      colorschemes.gruvbox = { enable = true; contrastDark = "soft"; };
      options = {
        # Copy indent from current line when starting a new line
        autoindent = true;
        # Automatically read open file if updates to it on storage have been
        # detected
        autoread = true;
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
        alpha.enable = true;
        # Buffer line (top, tabs)
        bufferline = { enable = true; enforceRegularTabs = true; };
        # File / AST breadcrumbs
        barbecue.enable = true;
        # Status line (bottom)
        lualine.enable = true;
        # File explorer (side)
        nvim-tree.enable = true;
        # File finder (popup)
        telescope.enable = true;
        # Language Server Protocol client
        lsp = {
          enable = true;
          keymaps.lspBuf = {
            "gd" = "definition";
            "gD" = "references";
            "gt" = "type_definition";
            "K" = "hover";
          };
          servers = {
            # Nix (nil with nixpkgs-fmt)
            nixd = {
              enable = true;
              settings.formatting.command = "${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt";
            };
            bashls.enable = true;
            gopls.enable = true;
            html.enable = true;
            jsonls.enable = true;
            pyright.enable = true;
            ruff-lsp.enable = true;
            tsserver.enable = true;
            yamlls.enable = true;
          };
        };
        # LSP formatting
        lsp-format.enable = true;
        treesitter.enable = true;
        # LSP completion
        nvim-cmp = {
          enable = true;
          mappingPresets = [ "insert" ];
          snippet.expand = "ultisnips";
        };
        # LSP pictograms
        lspkind.enable = true;
        # Git integration
        neogit.enable = true;
        # Git praise
        gitblame.enable = true;
        # Diff view
        diffview.enable = true;
        # Highlight other occurrences of word under cursor
        illuminate.enable = true;
        # Enable Nix language support
        nix.enable = true;
        # Enable working with TODO code comments
        todo-comments.enable = true;
        # Diagnostics, etc. 
        trouble.enable = true;
      };
      extraPlugins = with pkgs.vimPlugins; [ autoclose-nvim git-conflict-nvim ];
      extraConfigLua = ''
        require("autoclose").setup()
      '';
      keymaps = [
        { key = ";"; action = ":"; }
        { key = "<A-W>"; action = ":wall<CR>"; }
        { key = "<A-w>"; action = ":write<CR>"; }
        { key = "<A-x>"; action = ":bdelete<CR>"; }
        { key = "<A-{>"; action = ":BufferLineCyclePrev<CR>"; }
        { key = "<A-}>"; action = ":BufferLineCycleNext<CR>"; }
        { key = "<C-l>"; action = ":set invlist<CR>"; }
        { key = "<S-p>"; action = ":BufferLinePick<CR>"; }
        { key = "<S-s>"; action = ":sort<CR>"; }
        { key = "<Space>c"; action = ":nohlsearch<CR>"; }
        { key = "<leader>T"; action = ":Telescope find_files hidden=true<CR>"; }
        { key = "<leader>f"; action = ":NvimTreeToggle<CR>"; }
        { key = "<leader>g"; action = ":Telescope live_grep<CR>"; }
        { key = "<leader>t"; action = ":Telescope find_files<CR>"; }
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
      difftastic = { enable = true; background = "dark"; display = "inline"; };
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
          condition = "gitdir:~/src/git.usebasis.co/**";
        }
      ] ++ (lib.optionals (kernel == "darwin") [
        {
          path = "${config.xdg.configHome}/git/personal";
          condition = "gitdir:~/${srcDir}/github.com/**";
        }
        {
          path = "${config.xdg.configHome}/git/personal";
          condition = "gitdir:~/iCloud\ Drive/Development/github.com/**";
        }
      ]);
    };
    # Git terminal UI
    gitui.enable = true;
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
      config = { style = "full"; theme = "gruvbox-dark"; };
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
    zoxide = {
      enable = true;
      enableNushellIntegration = true;
      enableZshIntegration = true;
    };
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
  //
  # nix-darwin-only attributes
lib.attrsets.optionalAttrs (kernel == "darwin") {
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
}

