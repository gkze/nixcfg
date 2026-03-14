{
  config,
  lib,
  pkgs,
  slib,
  src,
  system,
  ...
}:
let
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    optionals
    types
    ;

  cfg = config.nixcfg.zsh;

  pluginType = types.submodule {
    options = {
      name = mkOption {
        type = types.str;
        description = "Plugin name in Home Manager zsh plugin entries.";
      };

      src = mkOption {
        type = types.oneOf [
          types.path
          types.str
        ];
        description = "Plugin source path or store path string.";
      };

      file = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Optional specific file to source from plugin src.";
      };
    };
  };

  mkPlugin =
    plugin:
    let
      file = plugin.file or null;
    in
    {
      inherit (plugin)
        name
        src
        ;
    }
    // optionalAttrs (file != null) { inherit file; };
in
{
  options.nixcfg.zsh = {
    enable = mkEnableOption "opinionated zsh shell configuration" // {
      default = true;
    };
    cdpath = mkOption {
      type = types.listOf types.str;
      default = [ (slib.srcDirBase system) ];
      description = "Directories used by zsh's CDPATH for quick navigation.";
    };
    historySize = mkOption {
      type = types.int;
      default = 100000;
      description = "Number of zsh history entries to keep and persist.";
    };
    includeRepoPlugins = mkOption {
      type = types.bool;
      default = true;
      description = "Enable local zsh plugins from this repository's misc/zsh-plugins directory.";
    };
    includeDockerCompletion = mkOption {
      type = types.bool;
      default = true;
      description = "Enable docker zsh completion generated from the docker CLI.";
    };
    includeAwsCompletion = mkOption {
      type = types.bool;
      default = true;
      description = "Enable AWS CLI zsh completion plugin.";
    };
    extraPlugins = mkOption {
      type = types.listOf pluginType;
      default = [ ];
      description = "Additional plugin attrsets appended to programs.zsh.plugins.";
    };
    extraInitContent = mkOption {
      type = types.lines;
      default = "";
      description = "Extra shell code appended to zsh initContent.";
    };
  };

  config = mkIf cfg.enable {
    programs.zsh = {
      enable = true;
      autocd = true;
      inherit (cfg) cdpath;
      dotDir = "${config.xdg.configHome}/zsh";
      enableVteIntegration = true;
      history = {
        append = true;
        expireDuplicatesFirst = true;
        extended = true;
        ignoreAllDups = true;
        path = "${config.xdg.configHome}/zsh/history";
        save = cfg.historySize;
        share = true;
        size = cfg.historySize;
      };
      completionInit = ''
        autoload -U compinit bashcompinit
        compinit && bashcompinit
        compdef _gpg gpg-sq
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

        export GPG_TTY=$(tty)

        ${cfg.extraInitContent}
      '';
      plugins = map mkPlugin (
        (with pkgs; [
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
            src = "${pkgs.zsh-system-clipboard}/share/zsh/zsh-system-clipboard";
          }
        ])
        ++ optionals cfg.includeRepoPlugins [
          {
            name = "zsh-vi-mode-backward-kill-word";
            src = "${src}/misc/zsh-plugins";
          }
          {
            name = "zsh-vi-mode-system-clipboard";
            src = "${src}/misc/zsh-plugins";
          }
        ]
        ++ optionals cfg.includeDockerCompletion [
          {
            name = "docker";
            src = pkgs.runCommand "_docker" { buildInputs = [ pkgs.docker ]; } ''
              mkdir $out
              docker completion zsh > $out/_docker
            '';
            file = "_docker";
          }
        ]
        ++ optionals cfg.includeAwsCompletion [
          {
            name = "aws";
            src = "${pkgs.awscli2}/share/zsh/site-functions";
            file = "_aws";
          }
        ]
        ++ cfg.extraPlugins
      );
    };
  };
}
