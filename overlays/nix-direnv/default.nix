{ final, prev, ... }:
let
  batchFlakeInputGcRootsPatch = builtins.toFile "nix-direnv-batch-flake-input-gcroots.patch" (
    builtins.readFile ./batch-flake-input-gcroots.patch
  );
  # nix-direnv is resolved in two stages, so patch the source-stage package
  # before passing it back through resholve.
  patchedUnresholved = prev.nix-direnv.unresholved.overrideAttrs (old: {
    patches = (old.patches or [ ]) ++ [ batchFlakeInputGcRootsPatch ];
  });
  patchedNixDirenv = prev.nix-direnv.overrideAttrs (old: {
    src = patchedUnresholved;
    passthru =
      let
        oldPassthru = old.passthru or { };
      in
      oldPassthru
      // {
        unresholved = patchedUnresholved;
        tests = (oldPassthru.tests or { }) // {
          batchedFlakeInputGcRoots = final.callPackage ./test.nix {
            nixDirenv = patchedNixDirenv;
          };
        };
      };
  });
in
{
  nix-direnv = patchedNixDirenv;
}
