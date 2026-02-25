{ config, lib, ... }:
let
  mkPathModule = import ./_path-module.nix { inherit config lib; };
in
mkPathModule {
  optionPath = [
    "nixcfg"
    "languages"
    "rust"
  ];
  enableDescription = "Rust toolchain (cargo bin path)";
  pathOptionName = "cargoBinPath";
  pathDefault = "$HOME/.cargo/bin";
  pathDescription = "Path to add to PATH for Cargo-installed binaries.";
}
