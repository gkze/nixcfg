{
  config,
  inputs,
  lib,
  pkgs,
  slib,
  system,
  userMeta,
  ...
}@args:
{
  imports = map (mod: import mod args) [
    {
      darwin = ./darwin.nix;
      linux = ./nixos.nix;
    }
    .${slib.kernel system}
    ./git.nix
    ./go.nix
    ./nixvim.nix
    ./packages.nix
    ./python.nix
    ./stylix.nix
    ./zsh.nix
  ];

  home = {
    file = {
      ".gnupg/gpg-agent.conf".text =
        let
          prog =
            {
              darwin = lib.getExe pkgs.pinentry_mac;
              linux = lib.getExe pkgs.pinentry;
            }
            .${slib.kernel pkgs.stdenv.hostPlatform.system};
        in
        ''
          pinentry-program ${prog}
        '';
      "${config.xdg.configHome}/ghostty/config".text = ''
        font-family = Hack Nerd Font Mono
        font-size = 11
        macos-option-as-alt = left
        keybind = alt+left=unbind
        theme = catppuccin-frappe
        window-height = 80
        window-width = 220
      '';
      ".local/bin" = {
        source = ./bin;
        recursive = true;
        executable = true;
      };
    };
    sessionPath = [
      "$HOME/.cargo/bin"
      "$HOME/.local/bin"
    ];
    sessionVariables = {
      DELTA_PAGER = "bat -p";
      EDITOR = "nvim";
      MANPAGER = "sh -c 'col -bx | bat -plman'";
      MANROFFOPT = "-c";
      NIX_PAGER = "bat -p";
      PAGER = "bat -p";
      LESS = "-R --mouse";
    };
    shellAliases =
      let
        ezaDefaultArgs = "-albghHistype -F --color=always --icons=always";
      in
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
  };

  languages = {
    go.enable = true;
    python.enable = true;
  };

  programs = {
    alacritty = {
      enable = true;
      settings = {
        terminal.shell = {
          program = lib.getExe pkgs.zellij;
          args = [
            "attach"
            "--create"
            "main"
          ];
        };
      };
    };
    awscli.enable = true;
    bat = {
      enable = true;
      syntaxes.kdl = {
        src = pkgs.sublime-kdl;
        file = "KDL1.sublime-syntax";
      };
      themes."Catppuccin Frappe" = {
        src = inputs.catppuccin-bat;
        file = "themes/Catppuccin Frappe.tmTheme";
      };
      config.theme = "Catppuccin Frappe";
    };
    bottom.enable = true;
    direnv = {
      enable = true;
      nix-direnv.enable = true;
      enableZshIntegration = true;
    };
    eza.enable = true;
    fzf = {
      enable = true;
      enableZshIntegration = true;
    };
    fd.enable = true;
    gitui = {
      enable = true;
      keyConfig = pkgs.fetchurl {
        url = slib.ghRaw {
          owner = "extrawurst";
          repo = "gitui";
          rev = "27e28d5f5141be43648b93dc05a164a08dd4ef96";
          path = "vim_style_key_config.ron";
        };
        hash = "sha256-uYL9CSCOlTdW3E87I7GsgvDEwOPHoz1LIxo8DARDX1Y=";
      };
    };
    gh = {
      enable = true;
      settings = {
        git_protocol = "ssh";
        editor = "nvim";
        prompt = "enabled";
        extensions = with pkgs; [ gh-dash ];
      };
    };
    ghostty = {
      enable = false;
      enableZshIntegration = true;
      installBatSyntax = true;
      installVimSyntax = true;
      settings = {
        font-family = "Hack Nerd Font Mono";
        font-size = 12;
        macos-option-as-alt = "left";
        keybind = "alt+left=unbind";
        theme = "catppuccin-frappe";
        window-height = 80;
        window-width = 220;
      };
    };
    gpg = {
      enable = true;
      homedir = "${config.xdg.dataHome}/gnupg";
      settings = {
        auto-key-retrieve = true;
        default-key = userMeta.gpg.keys.personal;
      };
    };
    helix = {
      enable = true;
      languages.language = [
        { name = "nix"; }
        { name = "python"; }
        { name = "bash"; }
      ];
    };
    home-manager.enable = true;
    jq.enable = true;
    man = {
      enable = true;
      generateCaches = true;
    };
    nushell.enable = true;
    nix-index.enable = true;
    ripgrep.enable = true;
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
      };
    };
    starship = {
      enable = true;
      enableZshIntegration = true;
      enableNushellIntegration = true;
      settings = {
        add_newline = false;
        line_break.disabled = true;
      };
    };
    topgrade = {
      enable = true;
      settings = {
        misc.disable = [
          "brew_cask"
          "brew_formula"
          "home_manager"
        ];
        git.repos = [ (slib.srcDirBase system) ];
        misc = {
          assume_yes = true;
          cleanup = true;
        };
      };
    };
    vscode.enable = true;
    yazi = {
      enable = true;
      enableZshIntegration = true;
      settings.manager.sort_by = "alphabetical";
    };
    zed-editor = {
      enable = true;
      userSettings.vim_mode = true;
    };
    zellij = {
      enable = true;
      settings = {
        keybinds.normal = {
          "bind \"Alt s\"".Clear = { };
          "bind \"Alt L\"".GoToNextTab = { };
          "bind \"Alt H\"".GoToPreviousTab = { };
        };
        session_serialization = false;
        simplified_ui = true;
      };
    };
    zoxide = {
      enable = true;
      enableNushellIntegration = true;
    };
  };
}
