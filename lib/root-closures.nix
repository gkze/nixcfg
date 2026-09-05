{
  lib,
  systems,
  darwinConfigurations ? { },
  nixosConfigurations ? { },
  homeConfigurations ? { },
  requiredKinds ? [ ],
  requiredRoots ? [ ],
}:
let
  supportedKinds = [
    "darwin"
    "nixos"
    "home"
  ];

  mkRoots =
    kind: closureFor: configurations:
    lib.mapAttrsToList (
      name: configuration:
      let
        closure = closureFor configuration;
      in
      {
        inherit
          closure
          kind
          name
          ;
        inherit (closure) system;
      }
    ) configurations;

  roots =
    mkRoots "darwin" (configuration: configuration.system) darwinConfigurations
    ++ mkRoots "nixos" (configuration: configuration.config.system.build.toplevel) nixosConfigurations
    ++ mkRoots "home" (configuration: configuration.activationPackage) homeConfigurations;

  rootKinds = lib.unique (map (root: root.kind) roots);
  unknownRequiredKinds = builtins.filter (kind: !(builtins.elem kind supportedKinds)) requiredKinds;
  missingRequiredKinds = builtins.filter (kind: !(builtins.elem kind rootKinds)) requiredKinds;
  missingRequiredRoots = builtins.filter (
    required: !(builtins.any (root: root.kind == required.kind && root.name == required.name) roots)
  ) requiredRoots;

  unsupportedSystems = lib.unique (
    map (root: root.system) (builtins.filter (root: !(builtins.elem root.system systems)) roots)
  );

  manifestRoots = map (root: {
    inherit (root) kind name system;
  }) roots;
in
assert lib.assertMsg (
  lib.unique requiredKinds == requiredKinds
) "required root closure kinds must be unique";
assert lib.assertMsg (unknownRequiredKinds == [ ]) (
  "unknown required root closure kinds: " + lib.concatStringsSep ", " unknownRequiredKinds
);
assert lib.assertMsg (missingRequiredKinds == [ ]) (
  "required root closure kinds have no configured roots: "
  + lib.concatStringsSep ", " missingRequiredKinds
);
assert lib.assertMsg (missingRequiredRoots == [ ]) (
  "required root closures are not configured: "
  + lib.concatStringsSep ", " (map (root: "${root.kind}:${root.name}") missingRequiredRoots)
);
assert lib.assertMsg (unsupportedSystems == [ ]) (
  "root closures use systems outside the shared system policy: "
  + lib.concatStringsSep ", " unsupportedSystems
);
{
  manifest = {
    schemaVersion = 2;
    inherit requiredKinds requiredRoots;
    roots = manifestRoots;
  };

  rootSystems = lib.unique (map (root: root.system) roots);

  forSystem =
    system:
    map (root: {
      name = "${root.kind}-${root.name}";
      path = root.closure;
    }) (builtins.filter (root: root.system == system) roots);
}
