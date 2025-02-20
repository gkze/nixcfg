{ lib, pkgs, ... }:
{
  home.packages =
    with pkgs;
    [
      ast-grep
      cachix
      coreutils
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
      moreutils
      nerd-fonts.hack
      nh
      obsidian
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
