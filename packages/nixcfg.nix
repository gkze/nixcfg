{
  inputs,
  flake-edit,
  mkResolvedBuildSystemsOverlay,
  nix-prefetch-git,
  symlinkJoin,
  python314,
  callPackage,
  lib,
  makeWrapper,
  installShellFiles,
  runCommand,
  ...
}:
let
  workspace = inputs.uv2nix.lib.workspace.loadWorkspace {
    workspaceRoot = ../.;
  };

  pySet =
    (callPackage inputs.pyproject-nix.build.packages {
      python = python314;
    }).overrideScope
      (
        lib.composeManyExtensions [
          inputs.pyproject-build-systems.overlays.default
          (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
          (mkResolvedBuildSystemsOverlay {
            nix-manipulator = {
              hatchling = [ ];
              hatch-vcs = [ ];
            };
            qprompt = {
              setuptools = [ ];
            };
            yattag = {
              setuptools = [ ];
            };
          })
        ]
      );

  venv = pySet.mkVirtualEnv "nixcfg-venv" workspace.deps.all;
in
symlinkJoin {
  name = "nixcfg";
  paths = [ venv ];
  nativeBuildInputs = [
    makeWrapper
    installShellFiles
  ];
  postBuild =
    let
      mkCompletionScript =
        shell:
        runCommand "nixcfg-completion-${shell}" { } ''
          ${venv}/bin/python \
            ${./nixcfg/render_completion.py} \
            ${lib.escapeShellArg shell} \
            > $out
        '';
    in
    ''
      wrapProgram $out/bin/nixcfg \
        --prefix PATH : ${
          lib.makeBinPath [
            flake-edit
            nix-prefetch-git
          ]
        }

      installShellCompletion --cmd nixcfg \
        --zsh ${mkCompletionScript "zsh"} \
        --bash ${mkCompletionScript "bash"} \
        --fish ${mkCompletionScript "fish"}
    '';
}
