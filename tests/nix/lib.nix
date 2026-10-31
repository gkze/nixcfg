# Shared assertion helpers for the Nix test suite.
#
# Every test in this directory follows the same shape: build a small synthetic
# input, collect assertions in a list, and terminate with
# `builtins.deepSeq checks true`. A failing assertion throws during evaluation,
# so an eval-only check fails before its stamp derivation is built.
#
# The helpers are pure `builtins` functions so a test can import them without
# threading a real `lib` through. Import the module, not the file, so adding a
# helper never changes an existing test signature:
#
#   let
#     inherit (import ./lib.nix) assertEq;
#   in ...
let
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  # Assert that evaluating `expression` throws. `builtins.tryEval` only catches
  # `throw` and `assert`, which is exactly the failure surface these tests
  # expect from validation code.
  assertThrows =
    label: expression:
    let
      result = builtins.tryEval expression;
    in
    if result.success then
      throw "${label}: expected an evaluation failure, got ${builtins.toJSON result.value}"
    else
      true;
in
{
  inherit assertEq assertThrows;
}
