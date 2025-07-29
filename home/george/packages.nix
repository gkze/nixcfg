{ lib, pkgs, ... }:
{
  home.packages =
    with pkgs;
    [
      # gitbutler
      # superfile
      (coreutils.override { minimal = false; })
      ast-grep
      cachix
      claude-code
      csvlens
      curl
      curlie
      czkawka
      dasel
      dbeaver-bin
      du-dust
      dua
      duf
      file
      gawk
      gita
      glab
      gnused
      gnutar
      gping
      graphviz
      grex
      httpie
      jnv
      jo
      killport
      mas
      mdq
      moreutils
      mountpoint-s3
      nerd-fonts.hack
      nh
      nodejs_latest
      procs
      red-reddit-cli
      rsync
      rustup
      sd
      sequoia-chameleon-gnupg
      sequoia-sq
      sequoia-sqop
      sequoia-sqv
      sequoia-wot
      slack
      slack-cli
      spacedrive
      stars
      tokei
      trdsql
      tree
      uv
      viddy
      xan
      xq-xml
      xz
      yq-go
    ]
    ++ lib.lists.optional pkgs.stdenv.isLinux wl-clipboard;
}
