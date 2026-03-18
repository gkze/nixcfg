# Override flakelight's auto-import to filter out darwin-only packages on
# non-darwin systems.  Without this, `nix flake check --all-systems` fails
# because nixpkgs' check-meta asserts on meta.platforms mismatches.
#
# When `packages/default.nix` exists, flakelight imports it directly instead
# of auto-importing the directory contents.  flakelight.importDir excludes
# default.nix, so there is no recursion.
{
  system ? null,
  flakelight,
  ...
}:
let
  discovery = import ../lib/discovery.nix;
  darwinOnly = [
    "commander"
    "codex-desktop"
    "conductor"
    "opencode-desktop-crate2nix-src"
    "zed-editor-nightly"
    "zed-editor-nightly-crate2nix-src"
  ];
  helperEntries = [
    "go-cli-wrapper"
    "openchamber-bun"
    "registry"
  ];
  sculptorSystems = [
    "aarch64-darwin"
    "x86_64-linux"
  ];
  supersetSystems = [
    "aarch64-darwin"
    "x86_64-linux"
  ];
  emdashSystems = [
    "aarch64-darwin"
    "aarch64-linux"
    "x86_64-linux"
  ];
  imported = flakelight.importDir ./.;
  crate2nixSourceEntries = discovery.discoverCompanionEntries {
    root = ./.;
    directories = builtins.attrNames imported;
    fileName = "crate2nix-src.nix";
  };
  all = imported // builtins.mapAttrs (_: import) crate2nixSourceEntries.entries;
  systemEval = builtins.tryEval system;
  resolvedSystem =
    if systemEval.success && systemEval.value != null then systemEval.value else "x86_64-linux";
  isDarwin = builtins.match ".*-darwin" resolvedSystem != null;
  unsupported =
    (if isDarwin then [ ] else darwinOnly)
    ++ (if builtins.elem resolvedSystem sculptorSystems then [ ] else [ "sculptor" ])
    ++ (if builtins.elem resolvedSystem supersetSystems then [ ] else [ "superset" ])
    ++ (if builtins.elem resolvedSystem emdashSystems then [ ] else [ "emdash" ])
    ++ helperEntries;
in
removeAttrs all unsupported
