{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (builtins)
    elem
    filter
    getAttr
    listToAttrs
    map
    ;
  inherit (lib) any nameValuePair;

  cfg = config.nixcfg.packageSets;

  # Package-set metadata and package membership live together here so the
  # option schema and resulting home.packages list stay in sync.
  packageSetTable = with pkgs; [
    {
      name = "core";
      description = "core utilities (coreutils, curl, gnused, etc.)";
      packages = [
        coreutils-full
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
      ];
    }
    {
      name = "nixTooling";
      description = "Nix development tools (cachix, nil, nixfmt, etc.)";
      packages = [
        cachix
        nil
        nix-output-monitor
        nix-tree
        nixfmt
      ];
    }
    {
      name = "gitExtensions";
      description = "Git extensions (git-bug, git-who, gita, glab)";
      packages = [
        git-bug
        git-who
        gita
        glab
      ];
    }
    {
      name = "dataProcessing";
      description = "data processing and query tools (crush, csvlens, jq, yq, etc.)";
      packages = [
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
      ];
    }
    {
      name = "security";
      description = "PGP and security tools (sequoia suite)";
      packages = [
        sequoia-chameleon-gnupg
        sequoia-sq
        sequoia-sqop
        sequoia-sqv
        sequoia-wot
      ];
    }
    {
      name = "monitoring";
      description = "system monitoring and diagnostics (dua, dust, procs, etc.)";
      packages = [
        dua
        duf
        dust
        gping
        killport
        ookla-speedtest
        procs
        tokei
        viddy
      ];
    }
    {
      name = "cliTools";
      description = "CLI tools and productivity (ast-grep, httpie, sentry-cli, etc.)";
      packages = [
        amp-cli
        ast-grep
        axiom-cli
        biome
        oxlint
        oxlint-tsgolint
        curator
        droid
        ffmpeg
        gmailctl
        gogcli
        graphviz
        grex
        httpie
        hermes-agent
        linear-cli
        sculptor
        sd
        sentry-cli
        taplo
        t3code
        toad
        worktrunk
        yt-dlp
      ];
    }
    {
      name = "guiApps";
      description = "GUI applications (code-cursor, slack, spotify, etc.)";
      packages = [
        code-cursor
        dbeaver-bin
        emdash
        jetbrains.datagrip
        hoppscotch
        config.fonts.monospace.package
        opencode-desktop
        postman
        red-reddit-cli
        slack
        spacedrive
        spotify
      ]
      ++ lib.optionals stdenv.isLinux [ wl-clipboard ]
      ++ lib.optionals stdenv.isDarwin [
        appcleaner
        chatgpt
        commander
        codex-desktop
        conductor
        opencode-desktop-electron-dev
        container
        cyberduck
        google-chrome
        granola
        iina
        mas
        notion-app
        raycast
        rapidapi
        sloth-app
        t3code-desktop
        wispr-flow
      ];
    }
    {
      name = "cloud";
      description = "cloud and infrastructure tools (mountpoint-s3)";
      packages = [
        mountpoint-s3
      ];
    }
    # Keep these out of the default host closures so cache-warming
    # priority and day-to-day installed packages stay aligned.
    {
      name = "heavyOptional";
      description = "large optional apps/tools that are expensive to keep in every host closure";
      packages =
        lib.optionals (stdenv.isDarwin && stdenv.hostPlatform.isAarch64) [
          goose-cli
        ]
        ++ [
          lumen
          czkawka
          mux
          scratch
          superset
        ];
    }
  ];

  packageSetOptions = listToAttrs (
    map (
      { name, description, ... }:
      nameValuePair name {
        enable = lib.mkEnableOption description // {
          default = true;
        };
      }
    ) packageSetTable
  );

  packageSetEnabled = packageSet: (getAttr packageSet.name cfg).enable;

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
  }
  // packageSetOptions;

  config.home.packages =
    let
      selected = lib.concatLists (
        map (packageSet: lib.optionals (packageSetEnabled packageSet) packageSet.packages) packageSetTable
      );
    in
    (filter (pkg: !(isExcluded pkg)) selected) ++ cfg.extraPackages;
}
