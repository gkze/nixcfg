{
  discoverDefaultNixEntries =
    {
      root,
      excludeFiles ? [ ],
      includeFile ? (_fileName: _stem: true),
    }:
    let
      entries = builtins.readDir root;
      entryNames = builtins.attrNames entries;

      stripNixSuffix = fileName: builtins.substring 0 ((builtins.stringLength fileName) - 4) fileName;

      dirNames = builtins.filter (
        name: entries.${name} == "directory" && builtins.pathExists (root + "/${name}/default.nix")
      ) entryNames;

      fileEntries =
        builtins.filter
          (entry: !(builtins.elem entry.stem dirNames) && includeFile entry.fileName entry.stem)
          (
            builtins.map
              (fileName: {
                inherit fileName;
                stem = stripNixSuffix fileName;
              })
              (
                builtins.filter (
                  fileName:
                  entries.${fileName} == "regular"
                  && builtins.match ".*\\.nix" fileName != null
                  && !(builtins.elem fileName excludeFiles)
                ) entryNames
              )
          );

      fileNames = builtins.map (entry: entry.stem) fileEntries;
    in
    {
      inherit
        dirNames
        fileEntries
        fileNames
        ;
      names = dirNames ++ fileNames;
      pathFor = name: if builtins.elem name dirNames then root + "/${name}" else root + "/${name}.nix";
    };

  discoverCompanionEntries =
    {
      root,
      directories,
      fileName,
      nameFor ? (
        dirName: "${dirName}-${builtins.substring 0 ((builtins.stringLength fileName) - 4) fileName}"
      ),
    }:
    let
      entryMap = builtins.listToAttrs (
        builtins.concatMap (
          dirName:
          let
            candidate = root + "/${dirName}/${fileName}";
          in
          if builtins.pathExists candidate then
            [
              {
                name = nameFor dirName;
                value = candidate;
              }
            ]
          else
            [ ]
        ) directories
      );
    in
    {
      entries = entryMap;
      names = builtins.attrNames entryMap;
      pathFor = name: entryMap.${name};
    };
}
