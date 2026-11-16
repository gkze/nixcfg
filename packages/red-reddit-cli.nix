{
  callPackage,
  callPackages,
  inputs,
  lib,
  python313,
  ...
}:
let
  inherit (inputs.red.inputs) pyproject-build-systems pyproject-nix uv2nix;
  platformCompat = import ../lib/pinned-input-platform-compat;
  # Copying a store path with builtins.path allocates a new hash on every
  # eval (Nix 2.35). The drv would then differ from itself and the closure
  # gate would rebuild this package on every pull request. The locked flake
  # input path is already content-addressed.
  redSource = inputs.red.outPath;
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = inputs.red; };
  pySet = (callPackage pyproject-nix.build.packages { python = python313; }).overrideScope (
    lib.composeManyExtensions [
      platformCompat.overlay
      pyproject-build-systems.overlays.default
      (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
      (_final: prev: {
        red-reddit-cli = prev.red-reddit-cli.overrideAttrs { src = redSource; };
      })
    ]
  );
in
(callPackages pyproject-nix.build.util { }).mkApplication {
  venv = (pySet.mkVirtualEnv "red" workspace.deps.default).overrideAttrs (old: {
    passthru = lib.recursiveUpdate (old.passthru or { }) {
      inherit (pySet.testing.passthru) tests;
    };
    meta = (old.meta or { }) // {
      mainProgram = "red";
    };
  });
  package = pySet.red-reddit-cli;
}
