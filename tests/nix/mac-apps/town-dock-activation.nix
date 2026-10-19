# Render the real Home Manager activation without evaluating a host or flake.
{ dockutil, homeDirectory }:
let
  lib = rec {
    attrByPath =
      path: fallback: attrs:
      if path == [ ] then
        attrs
      else if builtins.hasAttr (builtins.head path) attrs then
        attrByPath (builtins.tail path) fallback attrs.${builtins.head path}
      else
        fallback;
    concatMapStringsSep =
      separator: f: values:
      builtins.concatStringsSep separator (map f values);
    escapeShellArg = value: "'${builtins.replaceStrings [ "'" ] [ "'\\''" ] value}'";
    optionalAttrs = condition: attrs: if condition then attrs else { };
    mkMerge = builtins.foldl' (acc: value: acc // value) { };
    imap1 =
      f: values:
      builtins.genList (index: f (index + 1) (builtins.elemAt values index)) (builtins.length values);
    removeSuffix =
      suffix: value:
      builtins.substring 0 ((builtins.stringLength value) - (builtins.stringLength suffix)) value;
    hm.dag.entryAfter = _: text: { inherit text; };
  };
  result = import ../../../modules/darwin/george/town-dock-apps.nix {
    inherit lib;
    config.home = { inherit homeDirectory; };
    options.home.activation = { };
    pkgs = { inherit dockutil; };
    username = "test";
  };
in
result.home.activation.nixcfgTownDock.text
