{
  callPackage,
  protobuf_32,
  selfSource ? builtins.fromJSON (builtins.readFile ./sources.json),
  expectedNativeManifest ? import ./native-manifest.nix {
    protobuf = protobuf_32;
    paseoVersion = selfSource.version or null;
  },
  ...
}:
callPackage ./package.nix {
  inherit expectedNativeManifest selfSource;
}
