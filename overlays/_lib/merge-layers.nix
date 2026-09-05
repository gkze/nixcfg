{
  fragments,
  helpers,
  tinyOverlays,
}:
let
  laterLayers = helpers // tinyOverlays;
  shadowedFragmentKeys = builtins.attrNames (builtins.intersectAttrs fragments laterLayers);
  shadowedHelperKeys = builtins.attrNames (builtins.intersectAttrs helpers tinyOverlays);
in
if shadowedFragmentKeys != [ ] then
  throw (
    "Overlay fragments shadowed by later overlay layers: "
    + builtins.concatStringsSep ", " shadowedFragmentKeys
  )
else if shadowedHelperKeys != [ ] then
  throw (
    "Overlay helpers shadowed by tiny overlay layers: "
    + builtins.concatStringsSep ", " shadowedHelperKeys
  )
else
  fragments // laterLayers
