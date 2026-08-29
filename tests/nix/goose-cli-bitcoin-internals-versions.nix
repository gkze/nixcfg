let
  cargo = import ../../overlays/goose-cli/Cargo.nix {
    pkgs = { };
    lib = { };
    stdenv = { };
    rootSrc = ../..;
  };
  packageIds = builtins.filter (
    packageId: cargo.internal.crates.${packageId}.crateName == "bitcoin-internals"
  ) (builtins.attrNames cargo.internal.crates);
in
builtins.map (packageId: cargo.internal.crates.${packageId}.version) packageIds
