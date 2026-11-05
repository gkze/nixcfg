# Evaluation is required: the contract is that a missing upstream workspace
# returns null instead of throwing, and a present one is a store path.
{ lib }:
let
  copy = import ../../packages/superset/workspace-path.nix { inherit lib; };
  generated = ./superset-workspace-path/generated;
  copyFrom =
    upstreamSrc:
    copy {
      generatedRoot = "${toString generated}/";
      upstreamSrc = upstreamSrc;
    };
  kept = copyFrom ./superset-workspace-path/upstream-present (generated + "/apps/keep");
  dropped = copyFrom ./superset-workspace-path/upstream-missing (generated + "/apps/keep");
  outside = builtins.tryEval (
    copyFrom ./superset-workspace-path/upstream-present ./superset-workspace-path.nix
  );
in
assert lib.isStorePath (toString kept);
assert dropped == null;
assert !outside.success;
true
