{
  cacert,
  fetchPnpmDeps ? null,
  inputs,
  lib,
  nodejs,
  outputs,
  pnpm_11,
  pnpmConfigHook,
  stdenv,
  ...
}:
(import ../t3code/_shared.nix {
  inherit
    cacert
    fetchPnpmDeps
    inputs
    lib
    nodejs
    outputs
    pnpm_11
    pnpmConfigHook
    stdenv
    ;
  sourceHashPackageName = "t3code-workspace";
}).node_modules
