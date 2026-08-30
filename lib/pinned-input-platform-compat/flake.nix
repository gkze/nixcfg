{
  description = "Compatibility adapter for Curator's pinned rust-overlay";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs";
    rust-overlay = {
      url = "github:oxalica/rust-overlay/07d7dc6fcc5eae76b4fb0e19d4afd939437bec97";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { rust-overlay, ... }:
    let
      compat = import ./default.nix;
    in
    {
      overlays.default =
        final: prev:
        (rust-overlay.overlays.default (compat.withLegacyPlatformCallPackage final) (
          compat.withLegacyPlatformCallPackage prev
        ));
    };
}
