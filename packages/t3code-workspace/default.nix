{
  bun,
  cacert,
  inputs,
  lib,
  nodejs,
  outputs,
  stdenv,
  ...
}:
(import ../t3code/_shared.nix {
  inherit
    bun
    cacert
    inputs
    lib
    nodejs
    outputs
    stdenv
    ;
  sourceHashPackageName = "t3code-workspace";
}).node_modules
