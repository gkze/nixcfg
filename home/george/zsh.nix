{
  config,
  inputs,
  lib,
  pkgs,
  slib,
  src,
  system,
  ...
}:
{
  programs.zsh = {
    enable = true;
    autocd = true;
    cdpath = [ (slib.srcDirBase system) ];
    dotDir = "${config.xdg.configHome}/zsh";
    enableVteIntegration = true;
    # setOptions = [ "EXTENDED_GLOB" ];
    history = {
      expireDuplicatesFirst = true;
      extended = true;
      ignoreAllDups = true;
      path = "${config.xdg.configHome}/zsh/history";
      save = 100000;
      share = true;
      size = 100000;
    };
    completionInit = ''
      autoload -U compinit bashcompinit
      compinit && bashcompinit
    '';
    initContent = lib.mkOrder 550 ''
      unalias &>/dev/null run-help && autoload run-help
      zmodload zsh/complist zsh/zle
      zstyle ':completion:*' menu select
      autoload -Uz +X select-word-style
      select-word-style bash
      bindkey -M menuselect '^[[Z' reverse-menu-complete
      bindkey "^R" history-incremental-search-backward
      typeset -U PATH MANPATH

      compdef _gpg gpg-sq
    '';
    plugins = with pkgs; [
      {
        name = "zsh-autosuggestions";
        src = "${zsh-autosuggestions}/share/zsh-autosuggestions";
        file = "zsh-autosuggestions.zsh";
      }
      {
        name = "F-Sy-H";
        src = "${zsh-f-sy-h}/share/zsh/site-functions";
      }
      {
        name = "zsh-vi-mode";
        src = "${zsh-vi-mode}/share/zsh-vi-mode";
      }
      {
        name = "zsh-fzf-history-search";
        src = "${zsh-fzf-history-search}/share/zsh-fzf-history-search";
      }
      {
        name = "zsh-system-clipboard";
        src = inputs.zsh-system-clipboard;
      }
      {
        name = "zsh-vi-mode-backward-kill-word";
        src = "${src}/misc/zsh-plugins";
      }
      {
        name = "zsh-vi-mode-system-clipboard";
        src = "${src}/misc/zsh-plugins";
      }
      {
        name = "docker";
        src = runCommand "_docker" { buildInputs = [ docker ]; } ''
          mkdir $out
          docker completion zsh > $out/_docker
        '';
        file = "_docker";
      }
      {
        name = "aws";
        src = "${pkgs.awscli2}/share/zsh/site-functions";
        file = "_aws";
      }
    ];
  };
}
