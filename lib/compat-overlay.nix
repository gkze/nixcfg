{
  inputs ? { },
  outputs ? null,
  slib ? null,
  name ? "default",
  ...
}:
final: prev:
let
  resolvedSlib =
    if slib != null then
      slib
    else if outputs != null && outputs ? lib && outputs.lib != null then
      outputs.lib
    else
      throw "compat-overlay.nix: provide `slib` or `outputs.lib` before applying this overlay.";

  resolvedOutputs = if outputs != null then outputs else { lib = resolvedSlib; };

  overlaySet = import ../overlays/default.nix {
    inherit inputs;
    outputs = resolvedOutputs;
  };

  selectedOverlay =
    if builtins.hasAttr name overlaySet then
      overlaySet.${name}
    else
      throw "compat-overlay.nix: unknown overlay '${name}'";
in
selectedOverlay final prev
