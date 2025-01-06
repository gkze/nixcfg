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
      graphviz
      gnused
      gnutar
      gping
      hoppscotch
      httpie
      jnv
      moreutils
      # TODO: figure out
      nerd-fonts.hack
      nh
      obsidian
      procs
      rustup
      rsync
      sd
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
    ++ lib.lists.optional pkgs.stdenv.isLinux [ wl-clipboard ];
}
