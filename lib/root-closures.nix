{
  lib,
  systems,
  darwinConfigurations ? { },
  nixosConfigurations ? { },
  homeConfigurations ? { },
  declaredSystems ? null,
  requiredKinds ? [ ],
  requiredRoots ? [ ],
}:
let
  supportedKinds = [
    "darwin"
    "nixos"
    "home"
  ];
  configurations = {
    darwin = darwinConfigurations;
    nixos = nixosConfigurations;
    home = homeConfigurations;
  };

  mkRoots =
    kind: closureFor: kindConfigurations:
    lib.mapAttrsToList (
      name: metadata:
      let
        closure = closureFor kindConfigurations.${name};
      in
      {
        inherit
          closure
          kind
          name
          ;
        system = if declaredSystems == null then closure.system else metadata;
      }
    ) (if declaredSystems == null then kindConfigurations else declaredSystems.${kind} or { });

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
  mismatchedSystems = builtins.filter (root: root.system != root.closure.system) roots;
  mismatchedNames = builtins.filter (
    kind:
    declaredSystems != null
    && builtins.attrNames configurations.${kind} != builtins.attrNames (declaredSystems.${kind} or { })
  ) supportedKinds;
  validateClosures =
    value:
    assert lib.assertMsg (mismatchedNames == [ ]) (
      "root closure names differ from their declarations: " + lib.concatStringsSep ", " mismatchedNames
    );
    assert lib.assertMsg (mismatchedSystems == [ ]) (
      "root closure systems differ from their declarations: "
      + lib.concatStringsSep ", " (map (root: "${root.kind}:${root.name}") mismatchedSystems)
    );
    value;
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
  manifest = validateClosures {
    schemaVersion = 2;
    inherit requiredKinds requiredRoots;
    roots = manifestRoots;
  };

  # Discovery uses constructor metadata. Actual closure validation remains at
  # the manifest and root-check boundaries, where those closures are needed.
  rootSystems = lib.unique (map (root: root.system) roots);

  forSystem =
    system:
    validateClosures (
      map (root: {
        name = "${root.kind}-${root.name}";
        path = root.closure;
      }) (builtins.filter (root: root.system == system) roots)
    );
}
