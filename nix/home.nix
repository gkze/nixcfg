{ config, pkgs, ... }:
{
  imports = [ ./programs.nix ];

  home = {
    # This value determines the Home Manager release that your
    # configuration is compatible with. This helps avoid breakage
    # when a new Home Manager release introduces backwards
    # incompatible changes.
    #
    # You can update Home Manager without changing this value. See
    # the Home Manager release notes for a list of state version
    # changes in each release.
    stateVersion = "22.11";

    # Files
    file = {
      ".local/bin" = {
        source = ../local/bin;
        recursive = true;
        executable = true;
      };
      ".zsh" = { source = ../zsh; recursive = true; };
      # Needed for things that don't understand $ZDOTDIR easily
      ".zshenv".source = ../zsh/.zshenv;
    };

    # Packages that should be installed to the user profile.
    packages = with pkgs; [ ];
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

  # Let Home Manager install and manage itself.
  programs.home-manager.enable = true;

  # Configure GPG agent
  services.gpg-agent = { enable = pkgs.stdenv.hostPlatform.isLinux; };

  # User-local launchd agents
  launchd.agents = {
    ssh-add = {
      enable = pkgs.stdenv.hostPlatform.isDarwin;
      config = {
        Label = "org.openssh.add";
        LaunchOnlyOnce = true;
        RunAtLoad = true;
        ProgramArguments = [ "/usr/bin/ssh-add" "--apple-load-keychain" ];
      };
    };
    gpg-agent = {
      enable = pkgs.stdenv.hostPlatform.isDarwin;
      config = {
        Label = "org.gnupg.gpg-agent";
        RunAtLoad = true;
        ProgramArguments = [ "~/.nix-profile/bin/gpg-agent" "--server" ];
      };
    };
  };
}

