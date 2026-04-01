{ fragArgs, overlayDir }:
let
  stripNixSuffix = name: builtins.substring 0 ((builtins.stringLength name) - 4) name;
  entries = builtins.readDir overlayDir;
  entryNames = builtins.attrNames entries;
  fragDirs = builtins.filter (
    name: entries.${name} == "directory" && builtins.pathExists (overlayDir + "/${name}/default.nix")
  ) entryNames;
  fragFiles = builtins.filter (
    name:
    entries.${name} == "regular"
    && builtins.match ".*\\.nix" name != null
    && name != "default.nix"
    && !(builtins.elem (stripNixSuffix name) fragDirs)
  ) entryNames;

  # Overlay fragments with a sibling sources.json get that entry as
  # `selfSource`, which keeps single-source overlays from repeatedly indexing
  # into the shared sources attrset.
  withSelfSource =
    name:
    fragArgs
    // (
      if fragArgs ? sources && builtins.hasAttr name fragArgs.sources then
        {
          selfSource = fragArgs.sources.${name};
        }
      else
        { }
    );

  dirPairs = builtins.map (name: {
    inherit name;
    attrs = import (overlayDir + "/${name}") (withSelfSource name);
  }) fragDirs;
  filePairs = builtins.map (
    name:
    let
      fragmentName = stripNixSuffix name;
      attrs = import (overlayDir + "/${name}") (withSelfSource fragmentName);
    in
    if builtins.isAttrs attrs then
      {
        name = fragmentName;
        inherit attrs;
      }
    else
      null
  ) fragFiles;
  fragmentPairs = dirPairs ++ builtins.filter (pair: pair != null) filePairs;
  keyOwners = builtins.foldl' (
    acc: pair:
    acc
    // builtins.listToAttrs (
      builtins.map (key: {
        name = key;
        value = (acc.${key} or [ ]) ++ [ pair.name ];
      }) (builtins.attrNames pair.attrs)
    )
  ) { } fragmentPairs;
  duplicateKeys = builtins.filter (key: builtins.length keyOwners.${key} > 1) (
    builtins.attrNames keyOwners
  );
  # Keep this guard so fragment collisions fail early and deterministically.
  duplicateGuard =
    if duplicateKeys == [ ] then
      null
    else
      throw (
        "Duplicate overlay keys across fragments: "
        + builtins.concatStringsSep ", " (
          builtins.map (key: "${key} (" + builtins.concatStringsSep "/" keyOwners.${key} + ")") duplicateKeys
        )
      );
  fragments = builtins.map (pair: pair.attrs) fragmentPairs;
in
builtins.seq duplicateGuard (builtins.foldl' (acc: frag: acc // frag) { } fragments)
