{ lib, pkgs, ... }:
{
  home.packages =
    with pkgs;
    [
      (coreutils.override { minimal = false; })
      amp-cli
      ast-grep
      axiom-cli
      beads
      beads-mcp
      biome
      cachix
      cargo-update
      code-cursor
      crush
      csvlens
      curator
      curl
      curlie
      czkawka
      dasel
      dbeaver-bin
      deno
      droid
      dua
      duf
      dust
      file
      gawk
      git-bug
      git-who
      gita
      glab
      gmailctl
      gnused
      gnutar
      gogcli
      gping
      graphviz
      grex
      # home-manager - installed via programs.home-manager.enable
      hoppscotch
      httpie
      jetbrains.datagrip
      jnv
      jo
      killport
      linear-cli
      lumen
      mdq
      moreutils
      mountpoint-s3
      nerd-fonts.hack
      nil
      nix-output-monitor
      nix-tree
      nixfmt
      nodejs_latest
      # openchamber-desktop
      # openchamber-web
      opencode-desktop
      postman
      procs
      red-reddit-cli
      rsync
      rustup
      sd
      sentry-cli
      sequoia-chameleon-gnupg
      sequoia-sq
      sequoia-sqop
      sequoia-sqv
      sequoia-wot
      slack
      spacedrive
      spotify
      toad
      tokei
      trdsql
      tree
      viddy
      worktrunk
      xan
      xq-xml
      xz
      yq-go
    ]
    ++ lib.lists.optionals pkgs.stdenv.isLinux [
      # sculptor - temporarily removed (see overlays.nix)
      wl-clipboard
    ]
    ++ lib.lists.optionals pkgs.stdenv.isDarwin [
      appcleaner
      chatgpt
      conductor
      container
      cyberduck
      google-chrome
      iina
      mas
      notion-app
      rapidapi
      sloth-app
    ];
}
