# Overlay layer composition. A fragment that a later layer also defines is a
# silent behaviour change, so the merge refuses it rather than letting the later
# layer win. The merge order is tiny overlays over helpers over fragments.
{
  src ? ../..,
}:
let
  inherit (import ./lib.nix) assertEq assertThrows;

  mergeLayers = import (src + "/overlays/_lib/merge-layers.nix");

  merge =
    fragments: helpers: tinyOverlays:
    mergeLayers { inherit fragments helpers tinyOverlays; };

  checks = [
    (assertEq "distinct layer keys merge with later layers winning"
      {
        sourceBuilt = "fragment";
        helper = "helper";
        element-desktop = "managed-wrapper";
      }
      (
        merge { sourceBuilt = "fragment"; } { helper = "helper"; } {
          element-desktop = "managed-wrapper";
        }
      )
    )
    (assertThrows "a fragment shadowed by a tiny overlay is rejected" (
      merge { element-desktop = "obsolete-source-override"; } { } {
        element-desktop = "managed-wrapper";
      }
    ))
    (assertThrows "a fragment shadowed by a helper is rejected" (
      merge { shared = "fragment"; } { shared = "helper"; } { }
    ))
    (assertThrows "a helper shadowed by a tiny overlay is rejected" (
      merge { } { managed = "helper"; } { managed = "tiny-overlay"; }
    ))
    (assertEq "helpers win over fragments when no shadow is present" {
      helper = "helper";
      onlyFragment = "fragment";
    } (merge { onlyFragment = "fragment"; } { helper = "helper"; } { }))
  ];
in
builtins.deepSeq checks true
