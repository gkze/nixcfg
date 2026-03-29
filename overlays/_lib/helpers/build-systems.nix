_: rec {
  mkResolvedBuildSystemsOverlay =
    buildSystemsByPackage: pfinal: pprev:
    builtins.mapAttrs (
      packageName: buildSystems:
      pprev.${packageName}.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ pfinal.resolveBuildSystem buildSystems;
      })
    ) buildSystemsByPackage;

  mkSetuptoolsOverlay =
    packageNames:
    mkResolvedBuildSystemsOverlay (
      builtins.listToAttrs (
        builtins.map (packageName: {
          name = packageName;
          value = {
            setuptools = [ ];
          };
        }) packageNames
      )
    );
}
