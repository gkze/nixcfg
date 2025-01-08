{
  config,
  inputs,
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
  home.file."${config.xdg.configHome}/git/personal".text = ''
    [user]
      name = ${userMeta.name.user.github}
      email = ${userMeta.emails.personal}
  '';
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
          "sed"
          "'s/ /\t/'"
          "|"
          "column"
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
      gpg.program = "${pkgs.sequoia-chameleon-gnupg}/bin/gpg-sq";
      merge.conflictstyle = "diff3";
      rebase.pull = true;
      url."ssh://gitlab.gnome.org".insteadOf = "https://gitlab.gnome.org";
      user.signingkey = userMeta.gpg.keys.personal;
    };
    includes = [
      { path = "${inputs.catppuccin-delta}/catppuccin.gitconfig"; }
      {
        path = "${config.xdg.configHome}/git/personal";
        condition = "gitdir:${config.xdg.configHome}/nixcfg/**";
      }
      {
        path = "${config.xdg.configHome}/git/personal";
        condition = "gitdir:${config.xdg.configHome}/nixcfg-v2/**";
      }
      {
        path = "${config.xdg.configHome}/git/personal";
        condition = "gitdir:~/${slib.srcDirBase system}/github.com/**";
      }
    ];
  };
}
