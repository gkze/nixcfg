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
in
{
  home.file = {
    "${config.xdg.configHome}/git/personal".text = ''
      [user]
        name = ${userMeta.name.user.github}
        email = ${userMeta.emails.personal}
    '';
    "${config.xdg.configHome}/git/town".text = ''
      [user]
        name = ${userMeta.name.user.github}
        email = ${userMeta.emails.town}
    '';
  };
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
      includes =
        let
          srcDirBase = slib.srcDirBase system;
        in
        [
          { path = "${inputs.catppuccin-delta}/catppuccin.gitconfig"; }
          {
            path = "${config.xdg.configHome}/git/personal";
            condition = "gitdir:${config.xdg.configHome}/nixcfg/**";
          }
          {
            path = "${config.xdg.configHome}/git/town";
            condition = "gitdir:~/${srcDirBase}/github.com/townco/**";
          }
          {
            path = "${config.xdg.configHome}/git/personal";
            condition = "gitdir:~/${srcDirBase}/github.com/**";
          }
        ];
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
        delta.features = "catppuccin-frappe";
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
}
