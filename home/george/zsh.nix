{
  config,
  inputs,
  lib,
  pkgs,
  slib,
  system,
  ...
}:
let
  inherit (lib) optionalString;
  kernel = slib.kernel system;
in
{
  programs.zsh = {
    enable = true;
    autocd = true;
    cdpath = [ (slib.srcDirBase system) ];
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
    initExtraBeforeCompInit =
      ''''
      + optionalString (kernel == "darwin") ''
        HOMEBREW_PREFIX="/opt/homebrew"
        fpath+="''${HOMEBREW_PREFIX}/share/zsh/site-functions"
      '';
    completionInit = ''
      autoload -Uz +X compinit bashcompinit
      for dump in ''${ZDOTDIR}/.zcompdump(N.mh+24); do
        compinit && bashcompinit
      done
      compinit -C && bashcompinit -C
    '';
    initExtra =
      ''
        unalias &>/dev/null run-help && autoload run-help
        zmodload zsh/complist
        autoload -Uz +X select-word-style
        select-word-style bash
        zstyle ':completion:*' menu select
        bindkey -M menuselect '^[[Z' reverse-menu-complete
        bindkey "^R" history-incremental-search-backward
        typeset -U PATH MANPATH
      ''
      # Darwin-only Zsh configuration (post-compinit)
      + optionalString (kernel == "darwin") ''
        if [[ "$(uname -m)" != "arm64" ]]; then
          HOMEBREW_PREFIX="/usr/local"
        fi
        export PATH="''${HOMEBREW_PREFIX}/bin:''${PATH}"
        export MANPATH="''${HOMEBREW_PREFIX}/share/man:''${MANPATH}"
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
        name = "direnv";
        src = "${zsh-completions}/share/zsh/site-functions";
      }
      {
        name = "go";
        src = "${zsh-completions}/share/zsh/site-functions";
      }
    ];
  };
}
