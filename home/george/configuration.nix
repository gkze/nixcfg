{
  config,
  inputs,
  lib,
  pkgs,
  slib,
  system,
  userMeta,
  ...
}:
{
  imports = [
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

  sops = {
    gnupg.home = config.programs.gpg.homedir;
    defaultSopsFile = ../../secrets.yaml;
    environment.PATH = lib.mkForce (lib.makeBinPath [ pkgs.coreutils ] + ":/usr/bin:/sbin");
  };

  home = {
    file = {
      "${config.xdg.configHome}/zed/keymap.json".text = builtins.toJSON [
        {
          context = "Terminal";
          bindings = {
            "shift-enter" = [
              "terminal::SendText"
              "\u001b\r"
            ];
          };
        }
        {
          bindings = {
            "alt-~" = "terminal_panel::ToggleFocus";
          };
        }
      ];
      "${config.programs.gpg.homedir}/gpg-agent.conf".text =
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
      ".local/bin" = {
        source = ./bin;
        recursive = true;
        executable = true;
      };
    };
    sessionPath = [
      "$HOME/.bun/bin"
      "$HOME/.cargo/bin"
      "$HOME/.local/bin"
    ];
    sessionVariables = {
      DELTA_PAGER = "bat -p";
      EDITOR = "nvim";
      LESS = "-R --mouse";
      MANPAGER = "sh -c 'col -bx | bat -plman'";
      MANROFFOPT = "-c";
      NIX_PAGER = "bat -p";
      PAGER = "bat -p";
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
        zc = "[[ \"$(printenv)\" =~ \"VSCODE_|CURSOR_\" ]] && clear || zellij action clear";
        zj = "zellij";
        zq = "zellij kill-all-sessions --yes && zellij delete-all-sessions --force --yes";
      };
  };

  languages = {
    go.enable = true;
    python.enable = true;
  };

  programs = {
    # Programs with configuration (alphabetical)
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
    direnv = {
      enable = true;
      nix-direnv.enable = true;
      enableZshIntegration = true;
      config.warn_timeout = 0;
    };
    fzf = {
      enable = true;
      enableZshIntegration = true;
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
      enable = true;
      package = null; # installed via Homebrew on macOS
      enableZshIntegration = true;
      settings = {
        font-family = "Hack Nerd Font Mono";
        font-size = 12;
        macos-option-as-alt = "left";
        keybind = "alt+left=unbind";
        theme = "Catppuccin Frappe";
        window-height = 80;
        window-width = 220;
      };
    };
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
    man = {
      enable = true;
      generateCaches = true;
    };
    opencode = {
      enable = true;
      # MCP config is managed via sops template for secret injection
      # See sops.templates."opencode-config.json"
    };
    ssh = {
      enable = true;
      enableDefaultConfig = false;
      matchBlocks = {
        "github.com" = {
          compression = true;
          forwardAgent = true;
          hashKnownHosts = true;
          user = "git";
        };
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
      settings =
        let
          format = "[$symbol($version(-$name) )]($style) ";
        in
        {
          add_newline = false;
          aws.disabled = true;
          gcloud.format = "[$symbol$account(@$domain)(\($region\))]($style) ";
          line_break.disabled = true;
          nix_shell.format = "[$symbol$state( \($name\))]($style) ";
          nodejs = { inherit format; };
          python = { inherit format; };
        };
    };
    topgrade = {
      enable = true;
      settings = {
        misc.disable = [
          # "brew_cask"
          "brew_formula"
          "cursor"
        ];
        git.repos = [ (slib.srcDirBase system) ];
        misc = {
          assume_yes = true;
          cleanup = true;
        };
      };
    };
    yazi = {
      enable = true;
      enableZshIntegration = true;
      settings.manager.sort_by = "alphabetical";
    };
    zellij = {
      enable = true;
      enableZshIntegration = false;
      settings = {
        keybinds.normal = {
          "bind \"Alt s\"".Clear = { };
          "bind \"Alt L\"".GoToNextTab = { };
          "bind \"Alt H\"".GoToPreviousTab = { };
        };
        session_serialization = false;
        show_startup_tips = false;
        simplified_ui = true;
        scroll_buffer_size = 1000000;
      };
    };
    zoxide = {
      enable = true;
      enableNushellIntegration = true;
    };

    # Simple enable-only programs (alphabetical)
    awscli.enable = true;
    bottom.enable = true;
    bun.enable = true;
    codex.enable = true;
    discord.enable = true;
    element-desktop.enable = true;
    eza.enable = true;
    fd.enable = true;
    gemini-cli.enable = true;
    home-manager.enable = true;
    jq.enable = true;
    jujutsu.enable = true;
    less.enable = true;
    mergiraf.enable = true;
    neovide.enable = true;
    nh.enable = true;
    nix-index.enable = true;
    nushell.enable = true;
    ripgrep.enable = true;
    superfile.enable = true;
    uv.enable = true;
    vscode.enable = false;
  };
}
