{
  lib,
  rustBin,
  stdenv,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  toolchain = rustBin.stable."1.95.0".default;
in
lib.extendDerivation true {
  passthru = (toolchain.passthru or { }) // {
    buzzNativeContract = {
      kind = "rust-toolchain";
      channel = "1.95.0";
      profile = "default";
      target = "aarch64-apple-darwin";
    };
  };
} toolchain
