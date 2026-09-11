# Manual acceptance fixture: build only this tiny local fixed-output derivation.
# Pytest tests the Python orchestration at its subprocess boundary.
{ pkgs }:
pkgs.runCommand "nixcfg-prepared-fixed-output"
  {
    outputHash = pkgs.lib.fakeHash;
    outputHashAlgo = "sha256";
    outputHashMode = "flat";
  }
  ''
    printf %s 'nixcfg-prepared-fixed-output' > "$out"
  ''
