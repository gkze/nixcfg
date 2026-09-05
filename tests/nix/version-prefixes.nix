{ lib }:
let
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  checks = [
    (assertEq "leading v" "1.2.3" (lib.stripVersionPrefix "v1.2.3"))
    (assertEq "leading rust-v" "1.2.3" (lib.stripVersionPrefix "rust-v1.2.3"))
    (assertEq "leading desktop-v" "1.2.3" (lib.stripVersionPrefix "desktop-v1.2.3"))
    (assertEq "suffix v" "1.2.3v" (lib.stripVersionPrefix "v1.2.3v"))
    (assertEq "later v" "1.2.3-preview" (lib.stripVersionPrefix "v1.2.3-preview"))
  ];
in
builtins.deepSeq checks true
