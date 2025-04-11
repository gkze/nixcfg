{ lib, pkgs, ... }:
{
  home.packages =
    with pkgs;
    [
      (coreutils.override { minimal = false; })
      ast-grep
      cachix
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
      # gitbutler
      glab
      gnused
      gnutar
      gping
      graphviz
      grex
      hoppscotch
      httpie
      jnv
      mdq
      moreutils
      nerd-fonts.hack
      nh
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
      signal-desktop
      slack
      spacedrive
      stars
      superfile
      tokei
      trdsql
      uv
      viddy
      xan
      yq-go
    ]
    ++ lib.lists.optional pkgs.stdenv.isLinux wl-clipboard;
}
