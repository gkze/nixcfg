{
  discoverSidecarEntries =
    {
      root,
      fileName,
    }:
    let
      entries = if builtins.pathExists root then builtins.readDir root else { };
      entryNames = builtins.attrNames entries;

      suffix = ".${fileName}";
      suffixLen = builtins.stringLength suffix;
      hasSuffix =
        value:
        let
          valueLen = builtins.stringLength value;
        in
        valueLen >= suffixLen && builtins.substring (valueLen - suffixLen) suffixLen value == suffix;
      stripSuffix = value: builtins.substring 0 ((builtins.stringLength value) - suffixLen) value;

      dirEntries = builtins.listToAttrs (
        builtins.map
          (name: {
            inherit name;
            value = root + "/${name}/${fileName}";
          })
          (
            builtins.filter (
              name: entries.${name} == "directory" && builtins.pathExists (root + "/${name}/${fileName}")
            ) entryNames
          )
      );

      flatFileNames = builtins.filter (name: entries.${name} == "regular" && hasSuffix name) entryNames;
      flatEntries = builtins.listToAttrs (
        builtins.map (name: {
          name = stripSuffix name;
          value = root + "/${name}";
        }) flatFileNames
      );

      entryNameCollisions = builtins.filter (name: builtins.hasAttr name dirEntries) (
        builtins.attrNames flatEntries
      );
      collisionGuard =
        if entryNameCollisions == [ ] then
          null
        else
          throw (
            "Duplicate sidecar files under "
            + toString root
            + " for "
            + fileName
            + ": "
            + builtins.concatStringsSep ", " entryNameCollisions
          );
      sidecarEntries = builtins.seq collisionGuard (dirEntries // flatEntries);
    in
    {
      entries = sidecarEntries;
      names = builtins.attrNames sidecarEntries;
      pathFor = name: sidecarEntries.${name};
    };

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
