# Project the `mkDarwinHost` arguments that a host file composes from
# darwin/george/profiles.nix into JSON-compatible data. The stub `slib`
# returns the arguments unchanged, so no Nixpkgs or host evaluation runs.
{
  src ? ../..,
  host,
}:
let
  srcPrefix = toString src + "/";
  prefixLength = builtins.stringLength srcPrefix;
  relative =
    path:
    let
      value = toString path;
    in
    if builtins.substring 0 prefixLength value == srcPrefix then
      "./" + builtins.substring prefixLength (-1) value
    else
      value;
  project =
    value:
    if builtins.isPath value then
      relative value
    else if builtins.isFunction value then
      "<function>"
    else if builtins.isList value then
      map project value
    else if builtins.isAttrs value then
      builtins.mapAttrs (_: project) value
    else
      value;
  slib = {
    modulesPath = "<modulesPath>";
    mkDarwinHost = arguments: arguments;
  };
in
project (import (src + "/darwin/${host}.nix") { outputs.lib = slib; })
