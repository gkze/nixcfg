{ inputs, prev, ... }:
{
  mkUv2nixPackage =
    {
      name,
      src,
      pythonVersion ? prev.python314,
      mainProgram,
      packageName ? name,
      venvName ? name,
      uvLockFile ? builtins.path {
        name = "${name}-uv.lock";
        path = ../../../packages/${name}/uv.lock;
      },
      extraOverlays ? [ ],
    }:
    let
      uv2nixLib = inputs.uv2nix.lib;
      pyproject = prev.lib.importTOML (src + "/pyproject.toml");
      uvLock = uv2nixLib.lock1.parseLock (prev.lib.importTOML uvLockFile);
      localPackages = prev.lib.filter uv2nixLib.lock1.isLocalPackage uvLock.package;
      workspaceProjects = uv2nixLib.lock1.getLocalProjects {
        lock = uvLock;
        inherit localPackages;
        workspaceRoot = src;
      };
      workspaceConfig = uv2nixLib.workspace.loadConfig pyproject (
        (builtins.map (project: project.pyproject) (builtins.attrValues workspaceProjects))
        ++ prev.lib.optional (!(pyproject ? project)) pyproject
      );
      workspacePackages =
        prev.lib.mapAttrs
          (
            _name: packages:
            assert prev.lib.length packages == 1;
            prev.lib.head packages
          )
          (
            builtins.groupBy (package: package.name) (
              prev.lib.filter (package: !(package.source ? directory)) localPackages
            )
          );
      workspaceDeps = {
        all = prev.lib.mapAttrs (
          _: package:
          prev.lib.unique (
            builtins.attrNames package.optional-dependencies ++ builtins.attrNames package.dev-dependencies
          )
        ) workspacePackages;
      };
      pySet =
        (prev.callPackage inputs.pyproject-nix.build.packages {
          python = pythonVersion;
        }).overrideScope
          (
            prev.lib.composeManyExtensions (
              [
                inputs.pyproject-build-systems.overlays.default
                (uv2nixLib.overlays.mkOverlay {
                  sourcePreference = "wheel";
                  environ = { };
                  workspaceRoot = src;
                  localProjects = workspaceProjects;
                  spec = workspaceDeps.all;
                  lock = uvLock;
                  config = workspaceConfig;
                })
              ]
              ++ extraOverlays
            )
          );
    in
    (prev.callPackages inputs.pyproject-nix.build.util { }).mkApplication {
      venv = pySet.mkVirtualEnv venvName workspaceDeps.all // {
        meta.mainProgram = mainProgram;
      };
      package = pySet.${packageName};
    };
}
