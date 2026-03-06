{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (builtins) elem filter;
  inherit (lib) any;

  cfg = config.nixcfg.packageSets;

  isExcluded =
    pkg:
    let
      pkgNames = [
        (pkg.pname or null)
        (pkg.name or null)
      ];
    in
    any (name: name != null && elem name cfg.excludePackagesByName) pkgNames;
in
{
  options.nixcfg.packageSets = {
    extraPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ ];
      description = "Additional packages appended after all enabled package sets.";
    };

    excludePackagesByName = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Package pname/name values to remove from enabled package sets.";
    };

    core = {
      enable = lib.mkEnableOption "core utilities (coreutils, curl, gnused, etc.)" // {
        default = true;
      };
    };
    nixTooling = {
      enable = lib.mkEnableOption "Nix development tools (cachix, nil, nixfmt, etc.)" // {
        default = true;
      };
    };
    gitExtensions = {
      enable = lib.mkEnableOption "Git extensions (git-bug, git-who, gita, glab)" // {
        default = true;
      };
    };
    dataProcessing = {
      enable = lib.mkEnableOption "data processing and query tools (crush, csvlens, jq, yq, etc.)" // {
        default = true;
      };
    };
    security = {
      enable = lib.mkEnableOption "PGP and security tools (sequoia suite)" // {
        default = true;
      };
    };
    monitoring = {
      enable = lib.mkEnableOption "system monitoring and diagnostics (dua, dust, procs, etc.)" // {
        default = true;
      };
    };
    cliTools = {
      enable = lib.mkEnableOption "CLI tools and productivity (ast-grep, httpie, sentry-cli, etc.)" // {
        default = true;
      };
    };
    guiApps = {
      enable = lib.mkEnableOption "GUI applications (code-cursor, slack, spotify, etc.)" // {
        default = true;
      };
    };
    cloud = {
      enable = lib.mkEnableOption "cloud and infrastructure tools (mountpoint-s3)" // {
        default = true;
      };
    };
  };

  config.home.packages =
    let
      selected =
        with pkgs;
        lib.concatLists [
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

          (lib.optionals cfg.nixTooling.enable [
            cachix
            nil
            nix-output-monitor
            nix-tree
            nixfmt
          ])

          (lib.optionals cfg.gitExtensions.enable [
            git-bug
            git-who
            gita
            glab
          ])

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

          (lib.optionals cfg.security.enable [
            sequoia-chameleon-gnupg
            sequoia-sq
            sequoia-sqop
            sequoia-sqv
            sequoia-wot
          ])

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

          (lib.optionals cfg.cliTools.enable [
            amp-cli
            ast-grep
            axiom-cli
            biome
            curator
            droid
            ffmpeg
            gmailctl
            gogcli
            # goose-cli
            graphviz
            grex
            httpie
            linear-cli
            lumen
            sculptor
            sd
            sentry-cli
            taplo
            toad
            worktrunk
            yt-dlp
          ])

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

          (lib.optionals cfg.cloud.enable [
            mountpoint-s3
          ])
        ];
    in
    (filter (pkg: !(isExcluded pkg)) selected) ++ cfg.extraPackages;
}
