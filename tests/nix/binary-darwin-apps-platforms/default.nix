# Platform contract for overlays/registry-packages.nix, the registry-driven
# fragment that exposes registry packages through `overlays.default`.
# (The directory keeps its old name because flake.nix wires it as
# `test-nix-binary-darwin-apps-platforms`.)
{
  src ? ../../..,
}:
let
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  registry = import (src + "/packages/registry.nix") { inherit src; };

  # `callPackage` reports which package directory a call resolved, and which
  # flake context it forwarded, so the check never builds or imports nixpkgs.
  fragmentFor =
    system:
    import (src + "/overlays/registry-packages.nix") {
      inherit system;
      prev.lib = {
        genAttrs =
          names: f:
          builtins.listToAttrs (
            map (name: {
              inherit name;
              value = f name;
            }) names
          );
        setFunctionArgs = f: _args: f;
      };
      final.callPackage = _fn: args: { forwarded = builtins.attrNames args; };
      inputs = { };
      outputs = { };
    };
  names = attrs: builtins.sort builtins.lessThan (builtins.attrNames attrs);

  darwin = fragmentFor "aarch64-darwin";
  x86Linux = fragmentFor "x86_64-linux";
  armLinux = fragmentFor "aarch64-linux";
  linuxExtras = [
    "baseten"
    "codex"
    "gitbutler"
    "go-toolchain"
    "opencode-desktop"
    "pants-preview"
  ];
  darwinOnlyRegistryNames = builtins.filter (
    name:
    !(builtins.hasAttr name (registry.forSystem "x86_64-linux"))
    && !(builtins.hasAttr name (registry.forSystem "aarch64-linux"))
  ) (builtins.attrNames (registry.forSystem "aarch64-darwin"));

  checks = [
    (assertEq "x86_64-linux receives only the Linux-capable exports" linuxExtras (names x86Linux))
    (assertEq "aarch64-linux receives only the Linux-capable exports" linuxExtras (names armLinux))
    (assertEq "aarch64-darwin exposes every macOS-only registry package except overlay-owned ones" (
      builtins.filter
        (
          name:
          !(builtins.elem name [
            "commander"
            "raycast"
            "rio"
          ])
        )
        darwinOnlyRegistryNames
    ) (builtins.filter (name: !(builtins.elem name linuxExtras)) (names darwin)))
    (assertEq "registry-gated exports carry the registry's systems" true (
      darwin ? hq && !(x86Linux ? hq) && darwin ? zen-twilight
    ))
    (assertEq "exports forward flake context" [
      "inputs"
      "outputs"
    ] darwin.airfoil.forwarded)
  ];
in
builtins.deepSeq checks true
