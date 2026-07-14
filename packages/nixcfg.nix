{
  inputs,
  flake-edit,
  mkResolvedBuildSystemsOverlay,
  nix-prefetch-git,
  python314,
  callPackage,
  lib,
  makeWrapper,
  installShellFiles,
  runCommand,
  ...
}:
let
  # Filter the workspace source to only the files that participate in the
  # Python build (see [tool.setuptools] in pyproject.toml). Handing uv2nix the
  # unfiltered repo tree couples this derivation to every file in the repo,
  # which rebuilds the venv (and the system closure) on every commit.
  workspace = inputs.uv2nix.lib.workspace.loadWorkspace {
    workspaceRoot = lib.fileset.toSource {
      root = ../.;
      fileset = lib.fileset.unions [
        ../pyproject.toml
        ../uv.lock
        ../nixcfg.py
        (lib.fileset.fileFilter (file: file.hasExt "py" || file.hasExt "pyi") ../lib)
      ];
    };
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
runCommand "nixcfg"
  {
    nativeBuildInputs = [
      makeWrapper
      installShellFiles
    ];
    passthru = {
      inherit venv;
    };
  }
  (
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
      mkdir -p $out/bin

      makeWrapper ${venv}/bin/nixcfg $out/bin/nixcfg \
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
    ''
  )
