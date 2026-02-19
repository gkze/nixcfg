{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib) mkEnableOption;
  cfg = config.packageSets;
in
{
  options.packageSets = {
    core = {
      enable = mkEnableOption "core utilities (coreutils, curl, gnused, etc.)" // {
        default = true;
      };
    };
    nixTooling = {
      enable = mkEnableOption "Nix development tools (cachix, nil, nixfmt, etc.)" // {
        default = true;
      };
    };
    gitExtensions = {
      enable = mkEnableOption "Git extensions (git-bug, git-who, gita, glab)" // {
        default = true;
      };
    };
    dataProcessing = {
      enable = mkEnableOption "data processing & query tools (crush, csvlens, jq, yq, etc.)" // {
        default = true;
      };
    };
    security = {
      enable = mkEnableOption "PGP & security tools (sequoia suite)" // {
        default = true;
      };
    };
    monitoring = {
      enable = mkEnableOption "system monitoring & diagnostics (dua, dust, procs, etc.)" // {
        default = true;
      };
    };
    cliTools = {
      enable = mkEnableOption "CLI tools & productivity (ast-grep, httpie, sentry-cli, etc.)" // {
        default = true;
      };
    };
    guiApps = {
      enable = mkEnableOption "GUI applications (code-cursor, slack, spotify, etc.)" // {
        default = true;
      };
    };
    cloud = {
      enable = mkEnableOption "cloud & infrastructure tools (mountpoint-s3)" // {
        default = true;
      };
    };
  };

  config.home.packages =
    with pkgs;
    lib.concatLists [
      # Core utilities
      (lib.optionals cfg.core.enable [
        (coreutils.override { minimal = false; })
        curl
        curlie
        file
        gawk
        gnused
        gnutar
        moreutils
        rsync
        tree
        xz
      ])

      # Nix tooling
      (lib.optionals cfg.nixTooling.enable [
        cachix
        nil
        nix-output-monitor
        nix-tree
        nixfmt
      ])

      # Git extensions
      (lib.optionals cfg.gitExtensions.enable [
        git-bug
        git-who
        gita
        glab
      ])

      # Data processing & query tools
      (lib.optionals cfg.dataProcessing.enable [
        crush
        csvlens
        dasel
        jnv
        jo
        mdq
        trdsql
        xan
        xq-xml
        yq-go
      ])

      # Security & PGP
      (lib.optionals cfg.security.enable [
        sequoia-chameleon-gnupg
        sequoia-sq
        sequoia-sqop
        sequoia-sqv
        sequoia-wot
      ])

      # System monitoring & diagnostics
      (lib.optionals cfg.monitoring.enable [
        dua
        duf
        dust
        gping
        killport
        procs
        tokei
        viddy
      ])

      # CLI tools & productivity
      (lib.optionals cfg.cliTools.enable [
        amp-cli
        ast-grep
        axiom-cli
        beads
        beads-mcp
        biome
        curator
        droid
        gmailctl
        gogcli
        graphviz
        grex
        httpie
        linear-cli
        lumen
        sculptor
        sd
        sentry-cli
        toad
        worktrunk
      ])

      # GUI applications (cross-platform)
      (lib.optionals cfg.guiApps.enable (
        [
          code-cursor
          czkawka
          dbeaver-bin
          jetbrains.datagrip
          hoppscotch
          config.fonts.monospace.package
          opencode-desktop
          postman
          red-reddit-cli
          scratch
          slack
          spacedrive
          spotify
        ]
        ++ lib.optionals stdenv.isLinux [ wl-clipboard ]
        ++ lib.optionals stdenv.isDarwin [
          # macOS-only GUI applications
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
        ]
      ))

      # Cloud & infrastructure
      (lib.optionals cfg.cloud.enable [
        mountpoint-s3
      ])
    ];
}
