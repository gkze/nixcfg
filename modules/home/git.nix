{
  config,
  inputs,
  lib,
  pkgs,
  ...
}:
let
  inherit (builtins) concatStringsSep;

  cfg = config.nixcfg.git;
  identityCfg = cfg.identities;

  includeType = lib.types.submodule {
    options = {
      path = lib.mkOption {
        type = lib.types.str;
        description = "Path to include in git config.";
      };

      condition = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Optional git conditional include expression.";
      };
    };
  };

  mkInclude =
    include:
    {
      inherit (include) path;
    }
    // lib.optionalAttrs (include.condition != null) {
      inherit (include) condition;
    };
in
{
  options.nixcfg.git = {
    enable = lib.mkEnableOption "opinionated git and delta configuration" // {
      default = true;
    };
    signingKey = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Signing key passed to git as user.signingkey when set.";
    };

    signCommitsByDefault = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable commit.gpgsign in git config.";
    };

    enableDelta = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable the delta pager with git integration.";
    };

    includeCatppuccinDeltaTheme = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Include the catppuccin delta theme snippet.";
    };

    ignores = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ".direnv" ];
      description = "Global git ignore entries.";
    };

    extraIncludes = lib.mkOption {
      type = lib.types.listOf includeType;
      default = [ ];
      description = "Additional include entries appended to git includes.";
    };

    identities = lib.mkOption {
      type = lib.types.attrsOf (
        lib.types.submodule {
          options = {
            name = lib.mkOption {
              type = lib.types.str;
              description = "Git user name for this identity.";
            };
            email = lib.mkOption {
              type = lib.types.str;
              description = "Git email for this identity.";
            };
            conditions = lib.mkOption {
              type = lib.types.listOf lib.types.str;
              default = [ ];
              description = "Conditional include patterns (gitdir: format).";
            };
          };
        }
      );
      default = { };
      description = "Named git identities with conditional includes.";
    };
  };

  config = lib.mkIf cfg.enable {
    home.file = lib.mapAttrs' (
      id: idConfig:
      lib.nameValuePair "${config.xdg.configHome}/git/${id}" {
        text = ''
          [user]
            name = ${idConfig.name}
            email = ${idConfig.email}
        '';
      }
    ) identityCfg;

    programs = {
      delta = lib.mkIf cfg.enableDelta {
        enable = true;
        enableGitIntegration = true;
        options = {
          navigate = true;
          side-by-side = true;
        };
      };

      git = {
        enable = true;
        inherit (cfg) ignores;
        includes =
          lib.optionals cfg.includeCatppuccinDeltaTheme [
            { path = "${inputs.catppuccin-delta}/catppuccin.gitconfig"; }
          ]
          ++ lib.concatLists (
            lib.mapAttrsToList (
              id: idConfig:
              map (condition: {
                path = "${config.xdg.configHome}/git/${id}";
                inherit condition;
              }) idConfig.conditions
            ) identityCfg
          )
          ++ map mkInclude cfg.extraIncludes;
        lfs.enable = true;
        settings = {
          alias = {
            branches =
              let
                format = concatStringsSep "\t" [
                  "%(color:red)%(ahead-behind:HEAD)"
                  "%(color:blue)%(refname:short)"
                  "%(color:yellow)%(committerdate:relative)"
                  "%(color:default)%(describe)"
                ];
                header = concatStringsSep "," [
                  "Ahead"
                  "Behind"
                  "Branch Name"
                  "Last Commit"
                  "Description"
                ];
              in
              concatStringsSep " " [
                "!git for-each-ref"
                "--color"
                "--sort=-committerdate"
                "--format=$'${format}'"
                "refs/heads/"
                "--no-merged"
                "|"
                "${lib.getExe pkgs.gnused}"
                "'s/ /\t/'"
                "|"
                "${pkgs.util-linux}/bin/column"
                "--separator=$'\t'"
                "--table"
                "--table-columns='${header}'"
              ];
            praise = "blame";
          };
          commit.gpgsign = cfg.signCommitsByDefault;
          delta.features = config.theme.slug;
          diff.colorMoved = "default";
          fetch.prune = true;
          init.defaultBranch = "main";
          rebase.pull = true;
          url."ssh://gitlab.gnome.org".insteadOf = "https://gitlab.gnome.org";
        }
        // lib.optionalAttrs (cfg.signingKey != null) {
          user.signingkey = cfg.signingKey;
        };
        signing = {
          format = lib.mkForce "openpgp";
          signer = lib.getExe' pkgs.gnupg "gpg";
        };
      };
    };
  };
}
