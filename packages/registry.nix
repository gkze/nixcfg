{
  src ? ../.,
}:
let
  pkgDir = src + "/packages";
  discovery = import ../lib/discovery.nix;
  systemPolicy = builtins.fromJSON (builtins.readFile (src + "/lib/system-policy.json"));
  rootSystems =
    assert systemPolicy.schemaVersion == 1;
    builtins.attrNames systemPolicy.systems;

  systemsForCapability = predicate: builtins.filter predicate rootSystems;
  hasArchitecture = architecture: system: builtins.match "${architecture}-.*" system != null;
  hasKernel = kernel: system: builtins.match ".*-${kernel}" system != null;
  isAarch64Darwin = system: hasArchitecture "aarch64" system && hasKernel "darwin" system;
  isX86_64Linux = system: hasArchitecture "x86_64" system && hasKernel "linux" system;
  isSupportedLinux =
    system:
    (hasArchitecture "aarch64" system || hasArchitecture "x86_64" system) && hasKernel "linux" system;
  aarch64DarwinSystems = systemsForCapability isAarch64Darwin;
  darwinLinuxSystems = systemsForCapability (system: isAarch64Darwin system || isX86_64Linux system);
  nonX86DarwinLinuxSystems = systemsForCapability (
    system: isAarch64Darwin system || isSupportedLinux system
  );
  sculptorSystems = builtins.sort builtins.lessThan (darwinLinuxSystems ++ [ "x86_64-darwin" ]);

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
        "capy"
        "capy-nightly"
        "claude"
        "cleanshot"
        "clearly"
        "codeedit"
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
        "mole-app"
        "netnewswire"
        "raycast"
        "screen-studio"
        "signal-beta"
        "tembo"
        "thorium"
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
        "red-reddit-cli"
        "superset"
        "zed-editor-nightly"
        "zed-editor-nightly-crate2nix-src"
      ];
      nonX86DarwinLinuxPackages = [
        "baseten"
        "emdash"
        "opencode-desktop"
        "opencode-desktop-dev"
        "pants-preview"
      ];
    in
    metadataFor { helper = true; } helperPackages
    // constrainedTo "darwin" darwinPackages
    // constrainedTo aarch64DarwinSystems aarch64DarwinPackages
    // constrainedTo darwinLinuxSystems darwinLinuxPackages
    // constrainedTo nonX86DarwinLinuxSystems nonX86DarwinLinuxPackages
    // {
      sculptor = {
        constraint = sculptorSystems;
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
  # Which packages `overlays.default` exposes (overlays/registry-packages.nix).
  # The flake's own package set already contains every registry package; the
  # bare overlay is what the default.nix compatibility API and the update
  # probes in lib/update/nix.py apply. It carries the macOS packages, meaning
  # every package that no Linux root system supports, on the systems the
  # package's constraint allows.
  overlayExposure = {
    # Also exposed, on every system, although they build on Linux. Update
    # probes evaluate baseten's dependency hash through the overlay
    # (lib/update/updaters/core.py), and the others were exposed before the
    # registry owned this list. Every system is deliberate: on a system the
    # registry does not support (codex and gitbutler on aarch64-linux) the
    # attribute throws instead of silently falling back to the different
    # nixpkgs package of the same name.
    extraPackages = [
      "baseten"
      "codex"
      "gitbutler"
      "opencode-desktop"
      "pants-preview"
    ];
    # An overlay fragment (overlays/raycast, overlays/rio) or the tinyOverlays
    # in overlays/default.nix (commander) already defines the attribute; the
    # registry entry forwards to it, so exposing it again would collide.
    overlayOwnedPackages = [
      "commander"
      "raycast"
      "rio"
    ];
  };
  linuxRootSystems = builtins.filter (hasKernel "linux") rootSystems;
  isDarwinOnly =
    constraint: constraint != null && !(builtins.any (supportsSystem constraint) linuxRootSystems);
  unknownOverlayNames = builtins.filter (name: !(builtins.hasAttr name packagePaths)) (
    overlayExposure.extraPackages ++ overlayExposure.overlayOwnedPackages
  );
  darwinOverlayPackageNames =
    if unknownOverlayNames != [ ] then
      throw "packages/registry.nix: overlayExposure names no package: ${builtins.concatStringsSep ", " unknownOverlayNames}"
    else
      builtins.filter (
        name:
        !(builtins.elem name overlayExposure.overlayOwnedPackages)
        && !(builtins.elem name overlayExposure.extraPackages)
      ) (packageNamesMatching (meta: meta ? path && !meta.helper && isDarwinOnly meta.constraint));
in
{
  inherit
    darwinOnly
    helperEntries
    packagePaths
    sculptorSystems
    ;

  forSystem = system: packagePathsMatching (meta: supportsSystem meta.constraint system);

  overlayPackageNamesFor =
    system:
    let
      supported = packagePathsMatching (meta: supportsSystem meta.constraint system);
    in
    builtins.filter (name: builtins.hasAttr name supported) darwinOverlayPackageNames
    ++ overlayExposure.extraPackages;
}
