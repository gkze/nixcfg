# Platform contract for overlays/registry-packages.nix, the registry-driven
# fragment that exposes registry packages through `overlays.default`.
# (The directory keeps its old name because flake.nix wires it as
# `test-nix-binary-darwin-apps-platforms`.)
#
# Every expectation is derived from the registry, so adding or removing a
# package never touches this file. What is pinned is the shape of the
# projection: which systems receive which sets, and that the exposure rules
# documented in packages/registry.nix still hold.
{
  src ? ../../..,
}:
let
  inherit (import ../lib.nix) assertEq;

  registry = import (src + "/packages/registry.nix") { inherit src; };

  darwin = "aarch64-darwin";
  x86Linux = "x86_64-linux";
  armLinux = "aarch64-linux";
  linuxSystems = [
    x86Linux
    armLinux
  ];

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
  sorted = values: builtins.sort builtins.lessThan values;
  exportsFor = system: sorted (registry.overlayPackageNamesFor system);
  supportedFor = system: builtins.attrNames (registry.forSystem system);
  intersection = left: right: builtins.filter (name: builtins.elem name right) left;

  # A package the registry constrains to Darwin, in the sense that no Linux
  # root system satisfies its constraint.
  darwinOnly = builtins.filter (
    name: !(builtins.any (system: builtins.elem name (supportedFor system)) linuxSystems)
  ) (supportedFor darwin);

  # The fragment is a pure projection of the registry: it may not add, drop, or
  # reorder what `overlayPackageNamesFor` selects.
  projectionDrift = builtins.filter (system: names (fragmentFor system) != exportsFor system) (
    [ darwin ] ++ linuxSystems
  );

  # Packages exposed on a system the registry does not support are deliberate:
  # the attribute throws instead of silently resolving to the different nixpkgs
  # package of the same name (see the `extraPackages` comment in the registry).
  # That only stays safe while every root system receives the same extras.
  linuxParity = exportsFor x86Linux == exportsFor armLinux;

  # Everything exposed on Linux is also exposed on Darwin, because the extras
  # are platform-independent.
  linuxExports = exportsFor x86Linux ++ exportsFor armLinux;
  linuxReachableOnDarwin = builtins.all (name: builtins.elem name (exportsFor darwin)) linuxExports;

  # No package constrained to Darwin may leak into a Linux export set.
  darwinOnlyLeaked = intersection darwinOnly linuxExports;

  # Every exposed attribute was called with the flake context.
  contextNotForwarded = builtins.concatMap (
    system:
    builtins.filter (
      name:
      (fragmentFor system).${name}.forwarded != [
        "inputs"
        "outputs"
      ]
    ) (names (fragmentFor system))
  ) ([ darwin ] ++ linuxSystems);

  checks = [
    (assertEq "the fragment projects the registry exactly" [ ] projectionDrift)
    (assertEq "both Linux systems receive the same platform-independent extras" true linuxParity)
    (assertEq "every Linux export is also exposed on Darwin" true linuxReachableOnDarwin)
    (assertEq "no Darwin-only package leaks into a Linux export" [ ] darwinOnlyLeaked)
    (assertEq "every export forwards the flake context" [ ] contextNotForwarded)
  ];
in
builtins.deepSeq checks true
