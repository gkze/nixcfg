{
  lib,
  outputs,
}:
let
  maybeSelfSource =
    name:
    if
      outputs ? lib
      && outputs.lib ? sources
      && outputs.lib ? sourceEntry
      && builtins.hasAttr name outputs.lib.sources
    then
      outputs.lib.sourceEntry name
    else
      null;

  callPackageArgs =
    name:
    let
      selfSource = maybeSelfSource name;
    in
    if selfSource == null then { } else { inherit selfSource; };

  injectIntoFunction =
    name: pkg:
    let
      pkgArgs = builtins.functionArgs pkg;
      selfSource = maybeSelfSource name;
    in
    if !(pkgArgs ? selfSource) || selfSource == null then
      pkg
    else
      lib.setFunctionArgs (
        args:
        pkg (
          args
          // lib.optionalAttrs (!(args ? selfSource)) {
            inherit selfSource;
          }
        )
      ) (pkgArgs // { selfSource = true; });
in
{
  inherit
    callPackageArgs
    injectIntoFunction
    maybeSelfSource
    ;
}
