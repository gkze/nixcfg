{
  callPackage,
  protobuf_32,
  expectedNativeManifest ? import ./native-manifest.nix { protobuf = protobuf_32; },
  selfSource ? builtins.fromJSON (builtins.readFile ./sources.json),
  ...
}:
callPackage ./package.nix {
  inherit expectedNativeManifest selfSource;
}
