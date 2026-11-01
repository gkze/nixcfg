# Evaluates the real aarch64-darwin Superset source build: Superset 2.x
# (Electron 44) compiles better-sqlite3 from source, whose gyp-mac-tool needs
# `libtool`, and its dictation helper must be built with Xcode's swiftc under
# CI=1 so a missing toolchain fails the build instead of shipping without it.
{ pkgs }:
let
  inherit (import ../lib.nix) assertEq;

  inputPaths = map (input: input.drvPath) pkgs.superset.nativeBuildInputs;
  buildPhase = pkgs.superset.buildPhase or "";
  has =
    needle:
    builtins.match ".*${needle}.*" (builtins.replaceStrings [ "\n" ] [ " " ] buildPhase) != null;

  checks = [
    (assertEq "Superset provides cctools' libtool to node-gyp" true (
      builtins.elem pkgs.cctools.drvPath inputPaths
    ))
    (assertEq "Superset builds the transcriber with CI=1" true (
      has "CI=1 .* bun run --cwd apps/desktop bundle:transcriber"
    ))
    (assertEq "Superset requires the built transcriber" true (
      has "\\[ -x apps/desktop/dist/resources/bin/superset-transcriber ]"
    ))
  ];
in
builtins.deepSeq checks true
