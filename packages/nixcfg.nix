{
  inputs,
  deadnix,
  flake-edit,
  mkResolvedBuildSystemsOverlay,
  nix-prefetch-git,
  nixfmt,
  python314,
  callPackage,
  lib,
  makeWrapper,
  installShellFiles,
  runCommand,
  ...
}:
let
  platformCompat = import ../lib/pinned-input-platform-compat;
  runtimeSourcePolicy =
    (builtins.fromTOML (builtins.readFile ../pyproject.toml)).tool.nixcfg.runtimeSource;
  # Filter the workspace source to only the files that participate in the
  # Python build (see [tool.setuptools] in pyproject.toml). Handing uv2nix the
  # unfiltered repo tree couples this derivation to every file in the repo,
  # which rebuilds the venv (and the system closure) on every commit.
  runtimeSourceFiles =
    assert runtimeSourcePolicy.schemaVersion == 1;
    lib.fileset.unions [
      (lib.fileset.unions (map (path: ../. + "/${path}") runtimeSourcePolicy.rootPaths))
      (lib.fileset.difference
        (lib.fileset.fileFilter (
          file:
          lib.any (extension: file.hasExt extension) runtimeSourcePolicy.libraryExtensions
          || builtins.elem file.name runtimeSourcePolicy.libraryNames
        ) ../lib)
        (lib.fileset.unions (map (path: ../lib + "/${path}") runtimeSourcePolicy.excludedLibraryPaths))
      )
    ];
  dynamicUpdaterFiles = lib.fileset.unions (
    map (
      rootPath:
      lib.fileset.fileFilter (
        file:
        lib.any (extension: file.hasExt extension) runtimeSourcePolicy.dynamicExtensions
        && !lib.any (suffix: lib.hasSuffix suffix file.name) runtimeSourcePolicy.dynamicExcludedFileSuffixes
      ) (../. + "/${rootPath}")
    ) runtimeSourcePolicy.dynamicRoots
  );
  runtimeSource = lib.fileset.toSource {
    root = ../.;
    fileset = runtimeSourceFiles;
  };
  executionSource = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      runtimeSourceFiles
      dynamicUpdaterFiles
    ];
  };
  workspace = inputs.uv2nix.lib.workspace.loadWorkspace {
    workspaceRoot = runtimeSource;
  };

  pySet =
    (callPackage inputs.pyproject-nix.build.packages {
      python = python314;
    }).overrideScope
      (
        lib.composeManyExtensions [
          platformCompat.overlay
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
  ''
    mkdir -p $out/bin

    makeWrapper ${venv}/bin/nixcfg $out/bin/nixcfg \
      --set NIXCFG_UPDATE_EXECUTION_SOURCE ${executionSource} \
      --prefix PATH : ${
        lib.makeBinPath [
          deadnix
          flake-edit
          nix-prefetch-git
          nixfmt
        ]
      }

    for shell in zsh bash fish; do
      ${venv}/bin/python \
        ${./nixcfg/render_completion.py} \
        "$shell" \
        > "nixcfg-completion.$shell"
    done

    installShellCompletion --cmd nixcfg \
      --zsh nixcfg-completion.zsh \
      --bash nixcfg-completion.bash \
      --fish nixcfg-completion.fish
  ''
