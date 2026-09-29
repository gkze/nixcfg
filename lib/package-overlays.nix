# Keep host builds and updater probes on the same ordered package overlay set.
{ inputs, outputs }:
[
  inputs.devshell.overlays.default
  inputs.bun2nix.overlays.default
  inputs.curator.overlays.default
  inputs.lumen.overlays.default
  (import ../overlays/_lib/neovim-nightly-overlay.nix { inherit inputs; })
  inputs.rust-overlay.overlays.default
  inputs.nh.overlays.default
  outputs.overlays.default
]
