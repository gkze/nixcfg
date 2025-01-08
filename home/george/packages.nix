{ lib, pkgs, ... }:
{
  home.packages =
    with pkgs;
    [
      cachix
      curlie
      czkawka
      dasel
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
      superfile
      tokei
      # TODO: overlay
      # trdsql
      uv
      viddy
      yq-go
    ]
    ++ lib.lists.optional pkgs.stdenv.isLinux wl-clipboard;
}
