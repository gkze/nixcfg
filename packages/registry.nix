{
  src ? ../.,
}:
let
  pkgDir = src + "/packages";
  discovery = import ../lib/discovery.nix;

  discoveredPackages = discovery.discoverDefaultNixEntries {
    root = pkgDir;
    excludeFiles = [
      "default.nix"
      "registry.nix"
    ];
    includeFile = fileName: _: builtins.match "^_.*\\.nix$" fileName == null;
  };

  companionPackages = discovery.discoverCompanionEntries {
    root = pkgDir;
    directories = discoveredPackages.dirNames;
    fileName = "crate2nix-src.nix";
  };

  # Keep one internal metadata table so path discovery, helper exposure, and
  # platform guards cannot drift apart.
  basePackageMetadata =
    builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value = {
          path = discoveredPackages.pathFor name;
        };
      }) discoveredPackages.names
    )
    // builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value = {
          path = companionPackages.pathFor name;
        };
      }) companionPackages.names
    );

  metadataFor =
    attrs: names:
    builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value = attrs;
      }) names
    );

  constrainedTo = constraint: metadataFor { inherit constraint; };

  packageMetadataOverrides =
    let
      helperPackages = [
        "go-cli-wrapper"
        "registry"
        "t3code-workspace"
      ];
      darwinPackages = [
        "airfoil"
        "arc"
        "aside"
        "baseten-switch"
        "claude"
        "cleanshot"
        "clearly"
        "codeedit"
        "codex-desktop"
        "comet"
        "commander"
        "conductor"
        "factory"
        "figma"
        "framer"
        "granola"
        "grok-bot"
        "keepingyouawake"
        "linear"
        "loom"
        "macai"
        "mole-app"
        "netnewswire"
        "raycast"
        "screen-studio"
        "signal-beta"
        "tembo"
        "voiceos"
        "wispr-flow"
        "zen-twilight"
        "zo"
      ];
      aarch64DarwinPackages = [
        "agentastic-dev"
        "agentlog"
        "antigravity"
        "ara"
        "bb"
        "buzz"
        "claude-code"
        "claude-code-url-handler"
        "coast-local"
        "cogito"
        "docker-desktop"
        "energy"
        "executor"
        "freelens"
        "gemini"
        "ghostty-tip"
        "github-copilot-app"
        "gooeypi"
        "goose-desktop"
        "google-drive"
        "grok-build"
        "hq"
        "humanlayer"
        "hermes-desktop"
        "jacq"
        "logi-options-plus"
        "macfuse"
        "mach-studio"
        "nordvpn"
        "onepassword"
        "openchamber"
        "paseo"
        "pica"
        "reflect-open"
        "rio"
        "solo"
        "spotify"
        "superconductor"
        "tailscale-app"
        "t3code"
        "t3code-desktop"
        "todoist-desktop"
        "tolaria"
        "town-assistant-nightly"
        "traycer"
        "unsloth"
        "waku"
        "warp-preview"
        "wave"
        "writer-computer"
        "yaak-beta"
        "zeron"
      ];
      darwinLinuxPackages = [
        "codex"
        "codex-crate2nix-src"
        "codex-v8-native"
        "gitbutler"
        "gitbutler-crate2nix-src"
        "goose-cli"
        "goose-cli-crate2nix-src"
        "goose-cli-v8-native"
        "superset"
        "zed-editor-nightly"
        "zed-editor-nightly-crate2nix-src"
      ];
      nonX86DarwinLinuxPackages = [
        "baseten"
        "emdash"
        "pants-preview"
      ];
      allLocalSystemsPackages = [
        "opencode-desktop"
        "opencode-desktop-dev"
      ];
    in
    metadataFor { helper = true; } helperPackages
    // constrainedTo "darwin" darwinPackages
    // constrainedTo [ "aarch64-darwin" ] aarch64DarwinPackages
    // constrainedTo [
      "aarch64-darwin"
      "x86_64-linux"
    ] darwinLinuxPackages
    // constrainedTo [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ] nonX86DarwinLinuxPackages
    // constrainedTo [
      "aarch64-darwin"
      "x86_64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ] allLocalSystemsPackages
    // {
      sculptor = {
        constraint = [
          "aarch64-darwin"
          "x86_64-darwin"
          "x86_64-linux"
        ];
      };
    };

  packageMetadata = builtins.listToAttrs (
    builtins.map (name: {
      inherit name;
      value = {
        helper = false;
        constraint = null;
      }
      // (if builtins.hasAttr name basePackageMetadata then basePackageMetadata.${name} else { })
      // (
        if builtins.hasAttr name packageMetadataOverrides then packageMetadataOverrides.${name} else { }
      );
    }) (builtins.attrNames (basePackageMetadata // packageMetadataOverrides))
  );

  supportsSystem =
    constraint: system:
    if constraint == null then
      true
    else if builtins.isList constraint then
      builtins.elem system constraint
    else if constraint == "darwin" then
      builtins.match ".*-darwin" system != null
    else
      throw "packages/registry.nix: unsupported system constraint `${constraint}`";

  packageNamesMatching =
    predicate:
    builtins.filter (name: predicate packageMetadata.${name}) (builtins.attrNames packageMetadata);

  packagePathsMatching =
    predicate:
    builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value = packageMetadata.${name}.path;
      }) (packageNamesMatching (meta: meta ? path && !meta.helper && predicate meta))
    );

  packagePaths = packagePathsMatching (_meta: true);
  helperEntries = packageNamesMatching (meta: meta.helper);
  darwinOnly = packageNamesMatching (meta: meta.constraint == "darwin");
  sculptorSystems = packageMetadata.sculptor.constraint;
in
{
  inherit
    darwinOnly
    helperEntries
    packagePaths
    sculptorSystems
    ;

  forSystem = system: packagePathsMatching (meta: supportsSystem meta.constraint system);
}
