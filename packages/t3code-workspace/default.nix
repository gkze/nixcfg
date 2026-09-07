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
  t3codeWorkspaceSource ? null,
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
  inherit t3codeWorkspaceSource;
  sourceHashPackageName = "t3code-workspace";
}).node_modules
