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
  programs.git = {
    enable = true;
    aliases = {
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
    delta = {
      enable = true;
      options = {
        navigate = true;
        side-by-side = true;
      };
    };
    # difftastic = { enable = true; background = "dark"; };
    extraConfig = {
      commit.gpgsign = true;
      delta.features = "catppuccin-frappe";
      diff.colorMoved = "default";
      fetch.prune = true;
      merge.conflictstyle = "diff3";
      rebase.pull = true;
      url."ssh://gitlab.gnome.org".insteadOf = "https://gitlab.gnome.org";
      user.signingkey = userMeta.gpg.keys.personal;
    };
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
          path = "${config.xdg.configHome}/git/personal";
          condition = "gitdir:~/${srcDirBase}/github.com/**";
        }
        {
          path = "${config.xdg.configHome}/git/town";
          condition = "gitdir:~/${srcDirBase}/github.com/townco/**";
        }
      ];
    signing = {
      format = lib.mkForce "openpgp";
      signer = lib.getExe pkgs.sequoia-chameleon-gnupg;
    };
  };
}
