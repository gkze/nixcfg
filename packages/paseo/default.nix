{
  callPackage,
  expectedNativeManifest ? ./native-manifest.txt,
  selfSource ? builtins.fromJSON (builtins.readFile ./sources.json),
  ...
}:
callPackage ./package.nix {
  inherit expectedNativeManifest selfSource;
}
