{
  lib,
  src ? ../..,
}:
let
  slib = import (src + "/lib/lib.nix") {
    inherit lib src;
    inputs = { };
    outputs = { };
    pkgsFor = { };
  };
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";
  strings = builtins.fromJSON ''["\b", "\f", "\u0001", "\t\r\n", "\\\"", "Frappé"]'';
  checks =
    map (
      value: assertEq "JSONC string round trip" value (builtins.fromJSON (slib.toJSONC { } value))
    ) strings
    ++ [
      (assertEq "indentation and trailing commas remain stable"
        "{\n    \"demo\": [\n      1,\n      true,\n      null,\n    ],\n  }"
        (
          slib.toJSONC { initialIndent = 1; } {
            demo = [
              1
              true
              null
            ];
          }
        )
      )
      (assertEq "custom indentation" "[\n \"a\",\n]" (slib.toJSONC { indent = 1; } [ "a" ]))
      (assertEq "object keys use JSON escaping" "{\n  \"\\b\": [],\n}" (
        slib.toJSONC { } { ${builtins.head strings} = [ ]; }
      ))
      (assertEq "empty object" "{}" (slib.toJSONC { } { }))
      (assertEq "unsupported values still fail" false
        (builtins.tryEval (slib.toJSONC { } (value: value))).success
      )
    ];
in
# Escaping and string context belong to Nix's JSON implementation, not source spelling.
builtins.deepSeq checks true
