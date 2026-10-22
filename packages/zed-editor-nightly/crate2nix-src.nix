{
  callPackage,
  inputs,
  outputs,
  ...
}:
# The registry injects selfSource only into the package named after the
# sources.json entry, not into this companion, so pass the parent's entry.
callPackage ./default.nix {
  inherit inputs;
  selfSource = outputs.lib.sourceEntry "zed-editor-nightly";
  crate2nixSourceOnly = true;
}
