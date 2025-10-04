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
      container
      csvlens
      curl
      curlie
      cyberduck
      czkawka
      dasel
      dbeaver-bin
      discord
      du-dust
      dua
      duf
      element-desktop
      file
      gawk
      git-who
      gita
      glab
      gnused
      gnutar
      gping
      graphviz
      grex
      hoppscotch
      httpie
      jnv
      jo
      jujutsu
      killport
      less
      mas
      mdq
      moreutils
      mountpoint-s3
      neovide
      nerd-fonts.hack
      nh
      nil
      nix-output-monitor
      nix-tree
      nixfmt-rfc-style
      nodejs_latest
      postman
      procs
      rapidapi
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
      spotify
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
    ++ lib.lists.optionals pkgs.stdenv.isLinux [ wl-clipboard ]
    ++ lib.lists.optionals pkgs.stdenv.isDarwin [
      appcleaner
      chatgpt
      container
      notion-app
      sloth-app
    ];
}
