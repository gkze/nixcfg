{
  inputs,
  flake-edit,
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
          (pfinal: pprev: {
            nix-manipulator = pprev.nix-manipulator.overrideAttrs (old: {
              nativeBuildInputs =
                (old.nativeBuildInputs or [ ])
                ++ pfinal.resolveBuildSystem {
                  hatchling = [ ];
                  hatch-vcs = [ ];
                };
            });
            qprompt = pprev.qprompt.overrideAttrs (old: {
              nativeBuildInputs =
                (old.nativeBuildInputs or [ ])
                ++ pfinal.resolveBuildSystem {
                  setuptools = [ ];
                };
            });
            yattag = pprev.yattag.overrideAttrs (old: {
              nativeBuildInputs =
                (old.nativeBuildInputs or [ ])
                ++ pfinal.resolveBuildSystem {
                  setuptools = [ ];
                };
            });
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
          ${venv}/bin/python -c "
          from typer._completion_shared import get_completion_script
          print(get_completion_script(
              prog_name='nixcfg',
              complete_var='_NIXCFG_COMPLETE',
              shell='${shell}',
          ))
          " > $out
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
