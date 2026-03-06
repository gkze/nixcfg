# Override flakelight's auto-import to filter out darwin-only packages on
# non-darwin systems.  Without this, `nix flake check --all-systems` fails
# because nixpkgs' check-meta asserts on meta.platforms mismatches.
#
# When `packages/default.nix` exists, flakelight imports it directly instead
# of auto-importing the directory contents.  flakelight.importDir excludes
# default.nix, so there is no recursion.
{ system, flakelight, ... }:
let
  darwinOnly = [
    "codex-desktop"
    "conductor"
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
    "x86_64-linux"
  ];
  all = flakelight.importDir ./.;
  isDarwin = builtins.match ".*-darwin" system != null;
  unsupported =
    (if isDarwin then [ ] else darwinOnly)
    ++ (if builtins.elem system sculptorSystems then [ ] else [ "sculptor" ])
    ++ (if builtins.elem system supersetSystems then [ ] else [ "superset" ])
    ++ (if builtins.elem system emdashSystems then [ ] else [ "emdash" ])
    ++ helperEntries;
in
removeAttrs all unsupported
