{ config, pkgs, ... }:
{
  programs = {
    # Terminal emulator
    alacritty = {
      enable = true;
      settings = {
        draw_bold_text_with_bright_colors = false;
        font = { size = 12.0; normal = { family = "Hack Nerd Font Mono"; }; };
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
        {
          path = "~/.config/git/plaid";
          condition = "gitdir:~/Development/github.plaid.com/**";
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
      extraConfig = builtins.readFile ../config/tmux/tmux.conf;
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
        "github.plaid.com".user = "git";
        "github.com".user = "git";
        "*" = {
          extraOptions = {
            AddKeysToAgent = "yes";
            LogLevel = "ERROR";
            StrictHostKeyChecking = "no";
          };
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
        git = { repos = [ "~/Development/**" ]; };
      };
    };
  };
}
