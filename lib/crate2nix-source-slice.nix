let
  hasPrefix =
    prefix: value:
    let
      prefixLength = builtins.stringLength prefix;
    in
    builtins.stringLength value >= prefixLength && builtins.substring 0 prefixLength value == prefix;

  hasSuffix =
    suffix: value:
    let
      suffixLength = builtins.stringLength suffix;
      valueLength = builtins.stringLength value;
    in
    valueLength >= suffixLength
    && builtins.substring (valueLength - suffixLength) suffixLength value == suffix;

  sourcePath =
    rootSrc: relativePath: if relativePath == "." then rootSrc else "${rootSrc}/${relativePath}";

  materializeOne =
    {
      rootSrc,
      sourceFilter,
      relativePath,
      source,
      materializePath ? builtins.path,
    }:
    materializePath (
      {
        path = sourcePath rootSrc relativePath;
        inherit (source) name;
        filter = sourceFilter;
      }
      // (if source ? hash then { sha256 = source.hash; } else { })
    );
in
{
  sourceFilterLib = {
    inherit hasPrefix hasSuffix;
  };

  materialize =
    {
      rootSrc,
      sourceFilter,
      sources,
    }:
    builtins.mapAttrs (
      relativePath: source:
      materializeOne {
        inherit
          relativePath
          rootSrc
          source
          sourceFilter
          ;
      }
    ) sources;

  sourceFor =
    {
      rootSrc,
      source,
      sourceInfo,
      materializePath ? builtins.path,
    }:
    let
      sourceMatches = source == sourceInfo.source;
    in
    sourceFilter: relativePath:
    if sourceMatches then
      let
        slice =
          sourceInfo.slices.${relativePath}
            or (throw "crate source manifest has no slice for ${relativePath}");
        hashedSlice =
          if slice ? hash && builtins.isString slice.hash then
            slice
          else
            throw "crate source manifest slice ${relativePath} has no hash";
      in
      materializeOne {
        inherit
          materializePath
          relativePath
          rootSrc
          sourceFilter
          ;
        source = hashedSlice;
      }
    else
      throw "crate source manifest is stale; regenerate crate2nix artifacts";
}
