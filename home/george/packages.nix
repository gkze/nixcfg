{ lib, pkgs, ... }:
{
  home.packages =
    with pkgs;
    [
      ast-grep
      cachix
      (coreutils.override { minimal = false; })
      csvlens
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
      gitbutler
      glab
      gnused
      gnutar
      gping
      graphviz
      hoppscotch
      httpie
      jnv
      mdq
      moreutils
      nerd-fonts.hack
      nh
      procs
      rsync
      rustup
      sd
      sequoia-chameleon-gnupg
      sequoia-sq
      sequoia-sqop
      sequoia-sqv
      sequoia-wot
      slack
      spacedrive
      stars
      superfile
      tokei
      trdsql
      uv
      viddy
      yq-go
    ]
    ++ lib.lists.optional pkgs.stdenv.isLinux wl-clipboard;
}
