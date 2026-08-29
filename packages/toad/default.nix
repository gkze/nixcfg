{
  mkSetuptoolsOverlay,
  mkUv2nixPackage,
  inputs,
  python314,
  ...
}:
mkUv2nixPackage {
  name = "toad";
  src = inputs.toad;
  uvLockFile = ./uv.lock;
  pythonVersion = python314;
  mainProgram = "toad";
  packageName = "batrachian-toad";
  venvName = "batrachian-toad";
  extraOverlays = [
    (mkSetuptoolsOverlay [ "watchdog" ])
  ];
}
