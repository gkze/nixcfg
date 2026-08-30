{
  lib,
  nativeLock ? builtins.fromJSON (builtins.readFile ../native-lock.json),
  rustBin,
  stdenv,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  rustVersion = nativeLock.buzz.rustVersion or null;
  toolchain = rustBin.stable.${rustVersion}.default;
in
assert builtins.isString rustVersion;
lib.extendDerivation true {
  passthru = (toolchain.passthru or { }) // {
    buzzNativeContract = {
      kind = "rust-toolchain";
      channel = rustVersion;
      profile = "default";
      target = "aarch64-apple-darwin";
    };
  };
} toolchain
