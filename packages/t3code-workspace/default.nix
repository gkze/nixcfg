{
  bun,
  cacert,
  fetchPnpmDeps ? null,
  inputs,
  lib,
  nodejs,
  outputs,
  pnpm_10,
  pnpmConfigHook,
  stdenv,
  ...
}:
(import ../t3code/_shared.nix {
  inherit
    bun
    cacert
    fetchPnpmDeps
    inputs
    lib
    nodejs
    outputs
    pnpm_10
    pnpmConfigHook
    stdenv
    ;
  sourceHashPackageName = "t3code-workspace";
}).node_modules
