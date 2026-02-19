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
let
  inherit (builtins) concatStringsSep;
  inherit (lib) mkOption types;

  identityCfg = config.git.identities;
  srcDirBase = slib.srcDirBase system;
in
{
  options.git.identities = mkOption {
    type = types.attrsOf (
      types.submodule {
        options = {
          name = mkOption {
            type = types.str;
            description = "Git user name for this identity.";
          };
          email = mkOption {
            type = types.str;
            description = "Git email for this identity.";
          };
          conditions = mkOption {
            type = types.listOf types.str;
            default = [ ];
            description = "Conditional include patterns (gitdir: format).";
          };
        };
      }
    );
    default = { };
    description = "Named git identities with conditional includes.";
  };

  config = {
    # Default identities derived from userMeta
    git.identities = {
      personal = {
        name = lib.mkDefault userMeta.name.user.github;
        email = lib.mkDefault userMeta.emails.personal;
        conditions = lib.mkDefault [
          "gitdir:${config.xdg.configHome}/nixcfg/**"
          "gitdir:~/${srcDirBase}/github.com/**"
        ];
      };
      work = {
        name = lib.mkDefault userMeta.name.user.github;
        email = lib.mkDefault userMeta.emails.town;
        conditions = lib.mkDefault [
          "gitdir:~/${srcDirBase}/github.com/townco/**"
        ];
      };
    };

    # Generate identity config files
    home.file = lib.mapAttrs' (
      id: idCfg:
      lib.nameValuePair "${config.xdg.configHome}/git/${id}" {
        text = ''
          [user]
            name = ${idCfg.name}
            email = ${idCfg.email}
        '';
      }
    ) identityCfg;

    programs = {
      delta = {
        enable = true;
        enableGitIntegration = true;
        options = {
          navigate = true;
          side-by-side = true;
        };
      };
      git = {
        enable = true;
        ignores = [ ".direnv" ];
        includes = [
          { path = "${inputs.catppuccin-delta}/catppuccin.gitconfig"; }
        ]
        ++ lib.concatLists (
          lib.mapAttrsToList (
            id: idCfg:
            map (cond: {
              path = "${config.xdg.configHome}/git/${id}";
              condition = cond;
            }) idCfg.conditions
          ) identityCfg
        );
        lfs.enable = true;
        settings = {
          # merge.conflictstyle = "diff3";
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
          commit.gpgsign = true;
          delta.features = config.theme.slug;
          diff.colorMoved = "default";
          fetch.prune = true;
          init.defaultBranch = "main";
          rebase.pull = true;
          url."ssh://gitlab.gnome.org".insteadOf = "https://gitlab.gnome.org";
          # Use signing subkey explicitly for git signatures
          user.signingkey = userMeta.gpg.keys.signing;
        };
        signing = {
          format = lib.mkForce "openpgp";
          # TODO: switch back to sequoia-chameleon-gnupg once secret key access is fixed
          # See: https://gitlab.com/sequoia-pgp/sequoia-chameleon-gnupg/-/issues/156
          signer = lib.getExe' pkgs.gnupg "gpg";
        };
      };
    };
  };
}
