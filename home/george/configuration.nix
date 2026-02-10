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
    ./opencode.nix
    ./packages.nix
    ./python.nix
    ./stylix.nix
    ./zsh.nix
  ];

  sops = {
    gnupg.home = config.programs.gpg.homedir;
    defaultSopsFile = ../../secrets.yaml;
    environment.PATH = lib.mkForce (lib.makeBinPath [ pkgs.coreutils ] + ":/usr/bin:/sbin");
    secrets.github_pat = { };
    secrets.opencode_server_password = { };
  };

  home = {
    activation.injectZedGithubToken = lib.hm.dag.entryAfter [ "zedSettingsActivation" "sops-nix" ] ''
      # Inject sops-managed GitHub token into Zed settings (preserves user modifications)
      ${lib.getExe pkgs.jq} --arg token "$(cat ${config.sops.secrets.github_pat.path})" \
        '.context_servers."mcp-server-github".settings.github_personal_access_token = $token' \
        "${config.xdg.configHome}/zed/settings.json" \
        | ${lib.getExe' pkgs.moreutils "sponge"} "${config.xdg.configHome}/zed/settings.json"
    '';
    # Symlink ~/.config/home-manager -> nixcfg so `home-manager switch` uses the main flake
    # This avoids a separate lockfile that drifts when topgrade runs home-manager updates
    activation.standaloneHomeManagerSymlink = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      HM_DIR="${config.xdg.configHome}/home-manager"
      NIXCFG_DIR="${config.xdg.configHome}/nixcfg"

      # Only update if not already correctly symlinked
      if [ -L "$HM_DIR" ] && [ "$(readlink "$HM_DIR")" = "$NIXCFG_DIR" ]; then
        run --silence echo "home-manager symlink already correct"
      else
        # Remove existing directory/files
        if [ -e "$HM_DIR" ]; then
          run rm -rf "$HM_DIR"
        fi
        run ln -s "$NIXCFG_DIR" "$HM_DIR"
      fi
    '';
    file = {
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

  xdg.configFile."zen-folders.yaml".source = ./zen-folders.yaml;

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
      keyConfig =
        let
          source = slib.sourceHashEntry "gitui-key-config" "sha256";
        in
        pkgs.fetchurl {
          inherit (source) url;
          inherit (source) hash;
        };
    };
    gpg = {
      enable = true;
      homedir = "${config.xdg.dataHome}/gnupg";
      settings = {
        auto-key-retrieve = true;
        default-key = userMeta.gpg.keys.primary;
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
          StrictHostKeyChecking = "accept-new"; # Accept new keys, but alert on changes (MITM protection)
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
          # "cursor"
        ];
        git.repos = [ (slib.srcDirBase system) ];
        misc = {
          assume_yes = true;
          cleanup = true;
        };
      };
    };
    vscode = {
      enable = true;
      package = pkgs.vscode-insiders;
    };
    yazi = {
      enable = true;
      enableZshIntegration = true;
      settings.manager.sort_by = "alphabetical";
    };
    zellij = {
      enable = true;
      # Disabled: auto-attach behavior is disruptive; prefer manual invocation
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
    zed-editor = {
      enable = true;
      package = pkgs.zed-editor-nightly;
      userSettings = {
        agent_servers = {
          qwen-code.type = "registry";
          mistral-vibe.type = "registry";
          factory-droid.type = "registry";
          github-copilot.type = "registry";
          auggie.type = "registry";
          opencode.type = "registry";
        };
        agent = {
          default_model = {
            model = "claude-opus-4-5-20251101";
            provider = "anthropic";
          };
          dock = "right";
          inline_assistant_model = {
            model = "claude-opus-4-5-20251101";
            provider = "anthropic";
          };
          model_parameters = [ ];
        };
        buffer_font_family = "Hack Nerd Font Mono";
        buffer_font_size = 12.0;
        font_family = "Hack Nerd Font Mono";
        context_servers = {
          browser-tools-context-server = {
            enabled = true;
            remote = false;
            settings = { };
          };
          mcp-server-github = {
            enabled = true;
            settings = { }; # Token injected by activation script
          };
        };
        format_on_save = "on";
        icon_theme = "Catppuccin Frappé";
        minimap.show = "always";
        outline_panel.dock = "right";
        show_whitespaces = "all";
        theme = {
          dark = "Catppuccin Frappé";
          light = "One Light";
          mode = "system";
        };
        ui_font_family = "Cantarell";
        ui_font_size = 15.0;
        vim_mode = true;
        wrap_guides = [
          80
          100
        ];
      };
      userKeymaps = [
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
    nh = {
      enable = true;
      flake = "/Users/george/.config/nixcfg";
    };
    nix-index.enable = true;
    nushell.enable = true;
    ripgrep.enable = true;
    superfile.enable = true;
    uv.enable = true;
  };
}
