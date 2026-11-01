{
  src ? ../../..,
}:
let
  inherit (import ../lib.nix) assertEq assertThrows;

  toolchain = "rust-bin-1.99.1";

  fragmentWith =
    {
      system,
      selfSource ? {
        commit = "c";
        version = "0.5.29";
        pins.rustToolchain = "1.99.1";
      },
      stable ? {
        "1.99.1".default = toolchain;
      },
    }:
    import (src + "/overlays/rio") {
      final = {
        rust-bin = { inherit stable; };
        makeRustPlatform = platform: platform;
        callPackage = path: args: {
          overrideAttrs = _: {
            inherit path;
            inherit (args) rustPlatform;
          };
        };
      };
      prev = {
        lib.optionalAttrs = cond: attrs: if cond then attrs else { };
        stdenv.hostPlatform = { inherit system; };
        fetchFromGitHub = args: args;
        path = "/nixpkgs";
        # The registry forwarder (`{ rio, ... }: rio`) drops override args.
        rio.override = _: {
          overrideAttrs = _: { forwarder = true; };
        };
      };
      inherit selfSource;
      slib.sourceHash = _: _: "sha256-test";
    };

  fragmentFor = system: fragmentWith { inherit system; };

  checks = [
    (assertEq "x86_64-linux leaves nixpkgs Rio unchanged" { } (fragmentFor "x86_64-linux"))
    (assertEq "aarch64-linux leaves nixpkgs Rio unchanged" { } (fragmentFor "aarch64-linux"))
    (assertEq "x86_64-darwin leaves nixpkgs Rio unchanged" { } (fragmentFor "x86_64-darwin"))
    (assertEq "aarch64-darwin builds Rio with its pinned rust-toolchain.toml release" {
      path = "/nixpkgs/pkgs/by-name/ri/rio/package.nix";
      rustPlatform = {
        cargo = toolchain;
        rustc = toolchain;
      };
    } (fragmentFor "aarch64-darwin").rio)
    (assertThrows "a source entry without pins.rustToolchain fails evaluation"
      (fragmentWith {
        system = "aarch64-darwin";
        selfSource = {
          commit = "c";
          version = "0.5.29";
        };
      }).rio.rustPlatform.rustc
    )
    (assertThrows "a toolchain the rust-overlay pin lacks fails evaluation"
      (fragmentWith {
        system = "aarch64-darwin";
        stable = {
          "1.99.0".default = toolchain;
        };
      }).rio.rustPlatform.rustc
    )
  ];
in
builtins.deepSeq checks true
