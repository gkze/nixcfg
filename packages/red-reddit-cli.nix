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
  redSource = builtins.path {
    path = inputs.red;
    name = builtins.unsafeDiscardStringContext (builtins.baseNameOf (toString inputs.red));
  };
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = redSource; };
  pySet = (callPackage pyproject-nix.build.packages { python = python313; }).overrideScope (
    lib.composeManyExtensions [
      platformCompat.overlay
      pyproject-build-systems.overlays.default
      (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
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
