# Evaluation is needed to prove lazy reuse and override behavior across imports.
{ lib }:
let
  sourceA = ./t3code-workspace-source/a;
  sourceB = ./t3code-workspace-source/b;
  # pnpm reads the hook file and the lockfile's `file:` tarballs during a
  # frozen install; nothing else outside the manifests belongs in the input.
  lockedInputs =
    (import ../../packages/t3code/_source.nix {
      src = ./t3code-workspace-source/c;
      inherit lib;
    }).dependencySource;
  sharedSource =
    (import ../../packages/t3code/_source.nix {
      src = sourceA;
      inherit lib;
    })
    // {
      serverPackageJson.version = "cached";
    };
  args = {
    cacert = null;
    fetchPnpmDeps = value: value;
    inputs.t3code = sourceA;
    inherit lib;
    nodejs = "original-node";
    outputs.lib = {
      flakeLock.t3code.locked.rev = "abcdef012345";
      sourceHashForPlatform =
        name: hashType: system:
        "${name}:${hashType}:${system}";
    };
    pnpm_12.override = value: value;
    pnpmConfigHook = null;
    sourceHashPackageName = "t3code-workspace";
    stdenv = {
      hostPlatform.system = "aarch64-darwin";
      mkDerivation = value: value;
    };
    t3codeWorkspaceSource = sharedSource;
  };
  makeWorkspace = overrides: import ../../packages/t3code/_shared.nix (args // overrides);
  shared = makeWorkspace { };
  changedSource = makeWorkspace { inputs.t3code = sourceB; };
  changedToolchain = makeWorkspace { nodejs = "overridden-node"; };
  changedPolicy = makeWorkspace {
    outputs.lib = args.outputs.lib // {
      sourceHashForPlatform =
        _: _: _:
        "overridden-hash";
    };
    stdenv = args.stdenv // {
      hostPlatform.system = "x86_64-linux";
    };
  };
in
assert builtins.pathExists (lockedInputs + "/.pnpmfile.cjs");
assert builtins.pathExists (lockedInputs + "/apps/mobile/deps/local-1.0.0.tgz");
assert builtins.pathExists (lockedInputs + "/apps/mobile/deps/block-1.0.0.tgz");
assert !builtins.pathExists (lockedInputs + "/apps/mobile/deps/unlisted-2.0.0.tgz");
assert !builtins.pathExists (lockedInputs + "/README.md");
assert shared.version == "cached-main-abcdef0";
assert changedSource.version == "2.0.0-main-abcdef0";
assert changedSource.node_modules.src != shared.node_modules.src;
assert changedToolchain.node_modules.src == shared.node_modules.src;
assert changedToolchain.node_modules.pnpm.nodejs-slim == "overridden-node";
assert changedPolicy.node_modules.hash == "overridden-hash";
assert shared.node_modules.hash == "t3code-workspace:nodeModulesHash:aarch64-darwin";
true
