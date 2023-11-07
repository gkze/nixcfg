{ config, pkgs, lib, ... }:
{
  home = {
    # This value determines the Home Manager release that your
    # configuration is compatible with. This helps avoid breakage
    # when a new Home Manager release introduces backwards
    # incompatible changes.
    #
    # You can update Home Manager without changing this value. See
    # the Home Manager release notes for a list of state version
    # changes in each release.
    stateVersion = "23.11";

    # Files
    file = {
      ".local/bin" = {
        source = ../bin;
        recursive = true;
        executable = true;
      };
      ".zsh" = { source = ../zsh; recursive = true; };
      # Needed for things that don't understand $ZDOTDIR easily
      ".zshenv".source = ../zsh/.zshenv;
    };

    # Packages that should be installed to the user profile.
    # packages = with pkgs; [ ];
  };

  # Enable managing XDG Base Directories
  # Specification:
  # https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
  xdg = {
    enable = true;
    configFile = {
      "git" = { source = ../config/git; recursive = true; };
      "npmrc".source = ../config/npmrc;
      "nvim/lua" = { source = ../config/nvim/lua; recursive = true; };
      "pip/pip.conf".source = ../config/pip.conf;
      "sheldon/plugins.toml".source = ../config/sheldon.toml;
    };
  };

  # Activate Zsh configuration for Home Manager
  # programs.zsh.enable = true;
  programs = {
    # Let Home Manager install and manage itself.
    home-manager.enable = true;
    # nix-direnv is a faster and persistent implementaiton of direnv's use_nix
    # and use_flake
    direnv = { enable = true; nix-direnv.enable = true; };
    # Zsh <=> Nix integration
    zsh.enable = true;
  };

  # Configure GPG agent
  services.gpg-agent.enable = pkgs.stdenv.hostPlatform.isLinux;

  # User-local launchd agents (darwin only)
  launchd.agents = (lib.attrsets.optionalAttrs pkgs.stdenv.isDarwin {
    ssh-add = {
      enable = pkgs.stdenv.isDarwin;
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
    gpg-agent = {
      enable = pkgs.stdenv.isDarwin;
      config = {
        Label = "org.gnupg.gpg-agent";
        RunAtLoad = true;
        ProgramArguments = [ "~/.nix-profile/bin/gpg-agent" "--server" ];
      };
    };
  });

  # Configure installed software
  programs = {
    # Terminal emulator
    alacritty = {
      enable = true;
      settings = {
        draw_bold_text_with_bright_colors = false;
        font = { size = 12.0; normal.family = "Hack Nerd Font Mono"; };
        shell = { program = "${pkgs.tmux}/bin/tmux"; args = [ "attach" ]; };
        window = {
          # Remap Apple Option key to Alt key. Useful in Neovim for meta / alt
          option_as_alt = "Both";
          # Hand-sized out for MacBook Pro 16" so that the Alacritty window pops
          # up in the center with a bit of space left around the screen
          dimensions = { columns = 250; lines = 80; };
          position = { x = 306; y = 280; };
        };
        # Color configuration
        colors = {
          cursor = { cursor = "0xd5c4a1"; text = "0x1d2021"; };
          primary = { background = "0x1d2021"; foreground = "0xd5c4a1"; };
          bright = {
            black = "0x665c54";
            blue = "0xbdae93";
            cyan = "0xd65d0e";
            green = "0x3c3836";
            magenta = "0xebdbb2";
            red = "0xfe8019";
            white = "0xfbf1c7";
            yellow = "0x504945";
          };
          normal = {
            black = "0x1d2021";
            blue = "0x83a598";
            cyan = "0x8ec07c";
            green = "0xb8bb26";
            magenta = "0xd3869b";
            red = "0xfb4934";
            white = "0xd5c4a1";
            yellow = "0xfabd2f";
          };
        };
      };
    };
    # Bat is a more robust alternative to cat
    bat = { enable = true; config.theme = "gruvbox-dark"; };
    # Git configuration
    git = {
      enable = true;
      aliases.branches = "branch --sort=-committerdate --format='%(committerdate)\t::  %(refname:short)'";
      delta = {
        enable = true;
        options = {
          features = "decorations";
          navigate = true;
          decorations = {
            commit-decoration-style = "blue ol";
            commit-style = "raw";
            file-style = "omit";
            hunk-header-decoration-style = "blue box";
            hunk-header-file-style = "red";
            hunk-header-line-number-style = "#067a00";
            hunk-header-style = "file line-number syntax";
          };
        };
      };
      extraConfig = {
        commit.gpgsign = true;
        fetch.prune = true;
        merge.conflictstyle = "diff3";
        pager = {
          diff = "delta";
          log = "delta";
          reflog = "delta";
          show = "delta";
        };
        rebase.pull = true;
        user.signingkey = "9578FF9AB0BDE622307E7E833A7266FAC0D2F08D";
      };
      includes = [
        {
          path = "~/.config/git/personal";
          condition = "gitdir:~/Development/github.com/**";
        }
        {
          path = "~/.config/git/personal";
          condition = "gitdir:~/iCloud\ Drive/Development/github.com/**";
        }
        {
          path = "~/.config/git/personal";
          condition = "gitdir:~/.config/nixcfg/**";
        }
      ];
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
    # Neovim configuration. We only include the init Lua source and symlink the
    # rest as home manager files. We also only install the packer.nvim plugin
    # to get things going and hand off the rest of plugin management to it 
    neovim = {
      enable = true;
      defaultEditor = true;
      plugins = with pkgs.vimPlugins; [ packer-nvim ];
      extraLuaConfig = builtins.readFile ../config/nvim/init.lua;
    };
    # Tmux configuration
    tmux = {
      enable = true;
      sensibleOnTop = false;
      aggressiveResize = true;
      historyLimit = 10000;
      keyMode = "vi";
      mouse = true;
      newSession = true;
      prefix = "`";
      shell = "${pkgs.zsh}/bin/zsh";
      terminal = "xterm-256color";
      extraConfig = builtins.readFile ../config/tmux.conf;
    };
    # Starship configuration
    starship = {
      enable = true;
      # enableZshIntegration = true;
      settings = {
        add_newline = false;
        aws.disabled = true;
        gcloud.disabled = true;
        line_break.disabled = true;
        package.disabled = true;
        nix_shell.disabled = true;
        command_timeout = 2000;
      };
    };
    # SSH
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
          ServerAliveInterval = "120";
        };
        "rocinante" = {
          hostname = "10.0.0.241";
          user = "george";
          identityFile = "~/.ssh/personal.pem";
        };
      };
    };
    topgrade = {
      enable = true;
      settings = {
        assume_yes = true;
        cleanup = true;
        git.repos = [ "~/Development/**" ];
      };
    };
  };
}

