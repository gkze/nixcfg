{
  config,
  inputs,
  outputs,
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
    outputs.homeModules.nixcfgLanguageBun
    outputs.homeModules.nixcfgGit
    outputs.homeModules.nixcfgLanguageGo
    ./nixvim.nix
    ./zed.nix
    outputs.homeModules.nixcfgOpencode
    outputs.homeModules.nixcfgPackages
    outputs.homeModules.nixcfgZen
    outputs.homeModules.nixcfgLanguagePython
    outputs.homeModules.nixcfgLanguageRust
    outputs.homeModules.nixcfgStylix
    outputs.homeModules.nixcfgZsh
    inputs.catppuccin.homeModules.catppuccin
  ];

  sops = {
    gnupg.home = config.programs.gpg.homedir;
    defaultSopsFile = ../../secrets.yaml;
    environment.PATH = lib.mkForce (lib.makeBinPath [ pkgs.coreutils ] + ":/usr/bin:/sbin");
    secrets.github_pat = { };
    secrets.opencode_server_password = { };
  };

  home = {
    activation.materializeVscodeSettings =
      let
        vscodeSettingsHomeFileKey = "${config.home.homeDirectory}/Library/Application Support/${
          config.programs.vscode.nameShort or "Code - Insiders"
        }/User/settings.json";
        vscodeSettingsRelativePath = lib.removePrefix "${config.home.homeDirectory}/" vscodeSettingsHomeFileKey;
        vscodeSettingsSource = lib.attrByPath [
          "home"
          "file"
          vscodeSettingsHomeFileKey
          "source"
        ] (throw "missing generated VS Code settings source for ${vscodeSettingsHomeFileKey}") config;
      in
      lib.hm.dag.entryAfter [ "linkGeneration" ] ''
        settings_path="$HOME/${vscodeSettingsRelativePath}"
        settings_dir="$(dirname "$settings_path")"
        tmp_settings=""

        run mkdir -p "$settings_dir"
        tmp_settings="$(mktemp "$settings_dir/settings.json.tmp.XXXXXX")"

        cleanup_tmp() {
          if [ -n "$tmp_settings" ] && [ -e "$tmp_settings" ]; then
            rm -f "$tmp_settings"
          fi
        }

        trap cleanup_tmp EXIT
        run cp "${vscodeSettingsSource}" "$tmp_settings"
        run chmod 600 "$tmp_settings"
        run mv "$tmp_settings" "$settings_path"
        trap - EXIT
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
        if [ -L "$HM_DIR" ]; then
          run rm -f "$HM_DIR"
        elif [ -e "$HM_DIR" ]; then
          backup_path="$HM_DIR.nixcfg-backup.$(date +%Y%m%d%H%M%S)"
          echo "Backing up existing $HM_DIR to $backup_path" >&2
          run mv "$HM_DIR" "$backup_path"
        fi
        run ln -s "$NIXCFG_DIR" "$HM_DIR"
      fi
    '';
    file = {
      # Keep the Nix-generated VS Code settings content, but materialize it as a
      # normal file so the editor can mutate it between switches.
      "${config.home.homeDirectory}/Library/Application Support/${
        config.programs.vscode.nameShort or "Code - Insiders"
      }/User/settings.json".enable =
        false;

      "${config.programs.gpg.homedir}/gpg-agent.conf" =
        let
          pinentryProgram =
            {
              darwin = lib.getExe pkgs.pinentry_mac;
              linux = lib.getExe pkgs.pinentry;
            }
            .${slib.kernel pkgs.stdenv.hostPlatform.system};
          gpgconf = lib.getExe' pkgs.gnupg "gpgconf";
        in
        {
          text = ''
            pinentry-program ${pinentryProgram}
          '';
          onChange = ''
            GNUPGHOME=${lib.escapeShellArg config.programs.gpg.homedir} ${gpgconf} --kill gpg-agent
          '';
        };

      ".local/bin/git-ignore" = {
        source = ./bin/git-ignore;
        executable = true;
      };

      ".local/bin/but" = {
        source = "${pkgs.gitbutler}/bin/but";
        executable = true;
      };
    };
    sessionPath = [
      "$HOME/.local/bin"
    ];
    sessionVariables = {
      DELTA_PAGER = "bat -p";
      EDITOR = "nvim";
      LESS = "-R --mouse";
      MANPAGER = "sh -c 'col -bx | bat -plman'";
      MANROFFOPT = "-c";
      NIX_PAGER = "bat -p";
      OXLINT_TSGOLINT_PATH = lib.getExe pkgs.oxlint-tsgolint;
      OPENCODE_DB = "opencode.db";
      OPENCODE_EXPERIMENTAL = "1";
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

  nixcfg =
    let
      macAppHelpers = import ../../lib/mac-apps.nix { inherit lib pkgs; };

      # Keep the known-problem mutable macOS app bundles managed from /Applications
      # only so Launch Services has one canonical bundle to touch and old store
      # paths do not pick up App Management metadata that blocks garbage
      # collection. `excludePackageName` marks the entries that also need to stay
      # out of the GUI package set. VS Code Insiders still routes through
      # /Applications, but keeps its dedicated programs.vscode handling.
      managedMacAppRouting = [
        {
          excludePackageName = "chatgpt";
          package = pkgs.chatgpt;
          mode = "copy";
        }
        {
          excludePackageName = "cursor";
          package = pkgs.code-cursor;
          mode = "copy";
        }
        {
          excludePackageName = "datagrip";
          package = pkgs.jetbrains.datagrip;
          mode = "copy";
        }
        {
          package = pkgs.vscode-insiders;
          mode = "copy";
        }
        { package = pkgs.netnewswire; }
        { package = pkgs.gitbutler; }
        {
          excludePackageName = "wispr-flow";
          package = pkgs.wispr-flow;
        }
        { package = pkgs.zoom-us; }
        { package = pkgs.zen-twilight; }
      ]
      ++ lib.optionals config.profiles.work.enable [
        { package = pkgs.town-assistant-nightly; }
      ];
      managedMacAppProjection = macAppHelpers.managedMacAppRoutingProjection managedMacAppRouting;
    in
    {
      zen =
        let
          capitalize =
            s:
            (lib.toUpper (builtins.substring 0 1 s)) + (builtins.substring 1 (builtins.stringLength s - 1) s);
          themeDir =
            inputs.catppuccin-zen-browser
            + "/themes/${capitalize config.theme.variant}/${capitalize config.theme.accentColor}";
          logoName = "zen-logo-${config.theme.variant}.svg";
        in
        {
          enable = true;
          profile = "Default (twilight)";
          # Follow the forked Catppuccin branch pinned in flake.lock.
          chromeSource =
            assert builtins.pathExists (themeDir + "/userChrome.css");
            assert builtins.pathExists (themeDir + "/userContent.css");
            assert builtins.pathExists (themeDir + "/${logoName}");
            pkgs.runCommand "zen-catppuccin-theme-${config.theme.variant}-${config.theme.accentColor}" { } ''
              mkdir -p "$out"
              ln -s "${themeDir}/userChrome.css" "$out/userChrome.css"
              ln -s "${themeDir}/userContent.css" "$out/userContent.css"
              ln -s "${themeDir}/${logoName}" "$out/${logoName}"
            '';
          userJsSource = ./zen/user.js;
          foldersSource = ./zen/folders.yaml;
          quitDialogCssSource = ./zen/quit-dialog-primary.css;
        };

      languages = {
        bun.enable = true;
        go.enable = true;
        python.enable = true;
        rust.enable = true;
      };
      packageSets = {
        # Keep the standalone Home Manager config aligned with the Darwin host
        # closures so Linux CI never needs to realize heavyweight Darwin-only
        # optional apps just to evaluate unrelated checks.
        heavyOptional.enable = lib.mkDefault false;
        cloud.enable = lib.mkDefault false;
        inherit (managedMacAppProjection) excludePackagesByName;
        extraPackages = with pkgs; [
          betterdisplay
          rectangle
        ];
      };
      macApps.systemApplications = managedMacAppProjection.systemApplications;
      git = {
        signingKey = userMeta.gpg.keys.signing;
        identities = {
          personal = {
            name = userMeta.name.user.github;
            email = userMeta.emails.personal;
            conditions = [
              "gitdir:${config.xdg.configHome}/nixcfg/**"
              "gitdir:~/${slib.srcDirBase system}/github.com/**"
            ];
          };
          work = {
            name = userMeta.name.user.github;
            email = userMeta.emails.town;
            conditions = [
              "gitdir:~/${slib.srcDirBase system}/github.com/townco/**"
            ];
          };
        };
      };
      stylix.wallpaper = ./wallpaper.jpeg;
    };

  catppuccin = lib.mkIf (config.theme.name == "catppuccin") {
    flavor = config.theme.variant;
    accent = config.theme.accentColor;

    # Keep Stylix as the broad theming baseline, then selectively use
    # catppuccin/nix where we were previously unmanaged or where the dedicated
    # Catppuccin port gives us richer app-native theming.
    bottom.enable = true;
    eza.enable = true;
    element-desktop.enable = true;
    gemini-cli.enable = true;
    vscode.profiles.default = {
      enable = true;
      icons.enable = true;
    };
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
      themes.${config.theme.displayName} = {
        src = inputs.catppuccin-bat;
        file = "themes/${config.theme.displayName}.tmTheme";
      };
      config.theme = config.theme.displayName;
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
        font-family = config.fonts.monospace.name;
        font-size = 12;
        macos-option-as-alt = "left";
        keybind = "alt+left=unbind";
        theme = config.theme.displayName;
        window-height = 80;
        window-width = 220;
      };
    };
    gitui = {
      enable = true;
      keyConfig = "${inputs.gitui-key-config}/vim_style_key_config.ron";
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
    mergiraf = {
      enable = true;
      enableGitIntegration = true;
      enableJujutsuIntegration = true;
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
          gcloud.disabled = true;
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
          "brew_cask"
          "brew_formula"
          "home_manager"
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
      package = null;
      pname = "vscode-insiders";
    };
    yazi = {
      enable = true;
      enableZshIntegration = true;
      shellWrapperName = "y";
      settings.manager.sort_by = "alphabetical";
    };
    zellij = {
      enable = true;
      # Disabled: auto-attach behavior is disruptive; prefer manual invocation
      enableZshIntegration = false;
      # Stylix PR #2257 changed inactive Zellij ribbons to base05 text on
      # base02, but left emphasis_1 as base05. Zellij uses emphasis_1 as
      # the alternating inactive-tab background, which makes alternate tabs
      # render base05 text on base05 background. Keep alternating inactive
      # tabs visually disabled until Stylix adjusts it upstream.
      themes.stylix.themes.default.ribbon_unselected.emphasis_1 =
        lib.mkForce config.lib.stylix.colors.withHashtag.base02;
      settings = {
        keybinds = {
          normal = {
            "bind \"Alt b\"".TogglePaneFrames = { };
            "bind \"Alt s\"".Clear = { };
            "bind \"Alt L\"".GoToNextTab = { };
            "bind \"Alt H\"".GoToPreviousTab = { };
          };
          pane._children = [
            # Symmetric directional splits; Zellij core supports Left/Up even
            # though the shipped defaults only expose Down/Right shortcuts.
            {
              bind = {
                _args = [ "H" ];
                _children = [
                  { NewPane._args = [ "Left" ]; }
                  { SwitchToMode._args = [ "Normal" ]; }
                ];
              };
            }
            {
              bind = {
                _args = [ "J" ];
                _children = [
                  { NewPane._args = [ "Down" ]; }
                  { SwitchToMode._args = [ "Normal" ]; }
                ];
              };
            }
            {
              bind = {
                _args = [ "K" ];
                _children = [
                  { NewPane._args = [ "Up" ]; }
                  { SwitchToMode._args = [ "Normal" ]; }
                ];
              };
            }
            {
              bind = {
                _args = [ "L" ];
                _children = [
                  { NewPane._args = [ "Right" ]; }
                  { SwitchToMode._args = [ "Normal" ]; }
                ];
              };
            }
          ];
        };
        default_layout = "compact";
        pane_frames = false;
        session_serialization = false;
        show_startup_tips = false;
        simplified_ui = true;
        scroll_buffer_size = 1000000;
        ui.pane_frames.hide_session_name = true;
      };
    };
    zoxide = {
      enable = true;
      enableNushellIntegration = true;
    };

    # Simple enable-only programs (alphabetical)
    awscli.enable = true;
    bottom.enable = true;
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
    neovide.enable = true;
    nh = {
      enable = true;
      flake = config.nixcfg.flakePath;
    };
    nix-index.enable = true;
    nushell.enable = true;
    ripgrep.enable = true;
    superfile.enable = true;
    uv.enable = true;
  };
}
