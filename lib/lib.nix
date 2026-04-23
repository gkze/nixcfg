{
  inputs,
  lib,
  outputs,
  pkgsFor,
  src,
  ...
}:
let
  inherit (builtins)
    attrNames
    elemAt
    filter
    fromJSON
    getEnv
    hasAttr
    isList
    length
    listToAttrs
    map
    pathExists
    readDir
    readFile
    split
    ;
  inherit (lib.lists) findFirst optionals;
  inherit (lib.attrsets) optionalAttrs;

  isCI = getEnv "CI" != "";

  maybePath = p: if pathExists p then p else null;
  toList =
    value:
    if value == null then
      [ ]
    else if isList value then
      value
    else
      [ value ];
  userMetaPath = u: maybePath "${src}/home/${u}/meta.nix";
  modulesPath = "${src}/modules";
  crate2nixTauri = import ./crate2nix-tauri.nix { inherit lib; };
  rustyV8 = import ./rusty-v8.nix { inherit lib; };

  scanSourcesIn =
    dir:
    let
      entries = if pathExists dir then readDir dir else { };
      entryNames = attrNames entries;

      dirSources = listToAttrs (
        map
          (name: {
            inherit name;
            value = fromJSON (readFile (dir + "/${name}/sources.json"));
          })
          (
            filter (
              name: entries.${name} == "directory" && pathExists (dir + "/${name}/sources.json")
            ) entryNames
          )
      );

      suffix = ".sources.json";
      suffixLen = builtins.stringLength suffix;

      flatSourceFiles = filter (
        fileName: entries.${fileName} == "regular" && builtins.match ".*\\.sources\\.json" fileName != null
      ) entryNames;

      stripSourcesSuffix =
        fileName: builtins.substring 0 ((builtins.stringLength fileName) - suffixLen) fileName;

      flatSources = listToAttrs (
        map (fileName: {
          name = stripSourcesSuffix fileName;
          value = fromJSON (readFile (dir + "/${fileName}"));
        }) flatSourceFiles
      );

      sourceNameCollisions = filter (name: hasAttr name dirSources) (attrNames flatSources);

      sourceCollisionGuard =
        if sourceNameCollisions == [ ] then
          null
        else
          throw (
            "Duplicate source definitions under "
            + toString dir
            + ": "
            + builtins.concatStringsSep ", " sourceNameCollisions
          );
    in
    builtins.seq sourceCollisionGuard (dirSources // flatSources);
  packageSources = scanSourcesIn (src + "/packages") // scanSourcesIn (src + "/overlays");
  readSourceOverrides = raw: if raw == "" then { } else fromJSON raw;
  updateEvaluation = {
    sourceOverrides = readSourceOverrides (getEnv "UPDATE_SOURCE_OVERRIDES_JSON");
    fakeHashes = getEnv "FAKE_HASHES" == "1";
  };
in
rec {
  inherit (crate2nixTauri)
    mkCrate2nixTauriEnvOverride
    mkCrate2nixTauriOverrides
    mkCrate2nixTauriUtilsOverride
    tauriPluginEnvCrateNames
    ;
  inherit (rustyV8) mkRustyV8Build mkRustyV8PrebuiltArtifacts;
  inherit modulesPath;
  flakeLock = (fromJSON (readFile (src + "/flake.lock"))).nodes;
  # UPDATE_SOURCE_OVERRIDES_JSON allows update tooling to override selected
  # sources entries during evaluation without mutating tracked sources.json files.
  sources = packageSources // updateEvaluation.sourceOverrides;

  # When FAKE_HASHES=1, all sourceHash* functions return lib.fakeHash instead
  # of reading from sources.json.  This lets the update script evaluate the
  # overlay derivations with placeholder hashes to trigger hash-mismatch errors
  # from which the correct hashes are extracted — without duplicating any
  # derivation logic.
  fakeHashMode = updateEvaluation.fakeHashes;

  sourceEntry =
    name: if hasAttr name sources then sources.${name} else throw "sources.json missing for '${name}'";

  # Find hash entry matching hashType and optionally platform
  sourceHashEntry =
    name: hashType:
    if fakeHashMode then
      {
        hash = lib.fakeHash;
        inherit hashType;
        gitDep = "fake-dep";
      }
    else
      let
        entry = sourceEntry name;
        hashes = entry.hashes or [ ];
        matchEntry = findFirst (hash: hash.hashType == hashType && !(hash ? platform)) null hashes;
      in
      if matchEntry == null then throw "sources.json for '${name}' missing ${hashType}" else matchEntry;

  # Find hash entry matching hashType and specific platform
  sourceHashEntryForPlatform =
    name: hashType: platform:
    if fakeHashMode then
      {
        hash = lib.fakeHash;
        inherit hashType;
        inherit platform;
      }
    else
      let
        entry = sourceEntry name;
        hashes = entry.hashes or [ ];
        matchEntry = findFirst (
          hash: hash.hashType == hashType && (hash.platform or null) == platform
        ) null hashes;
      in
      if matchEntry == null then
        throw "sources.json for '${name}' missing ${hashType} on ${platform}"
      else
        matchEntry;

  sourceHash = name: hashType: (sourceHashEntry name hashType).hash;

  # Get hash for a specific platform
  sourceHashForPlatform =
    name: hashType: platform:
    (sourceHashEntryForPlatform name hashType platform).hash;
  sourceUrl =
    name: hashType:
    let
      entry = sourceHashEntry name hashType;
    in
    entry.url or (throw "sources.json for '${name}' missing url for ${hashType}");

  normalizeName = s: builtins.replaceStrings [ "." "_" ] [ "-" "-" ] s;

  # Helper to strip version prefixes from flake refs
  stripVersionPrefix = s: builtins.replaceStrings [ "rust-v" "v" ] [ "" "" ] s;

  # Get version from flake lock ref, stripping common prefixes.
  # This expects the input to be pinned with an `original.ref` (tag/branch).
  getFlakeVersion =
    name:
    let
      node = flakeLock.${name};
      ref = node.original.ref or null;
    in
    if ref == null then
      throw "flake.lock input '${name}' missing original.ref (pin with a tag/branch ref to use getFlakeVersion)"
    else
      stripVersionPrefix ref;

  # Convert a Nix value to JSONC format with trailing commas
  toJSONC =
    {
      indent ? 2,
      initialIndent ? 0,
    }:
    value:
    let
      inherit (builtins)
        attrNames
        concatStringsSep
        isBool
        isList
        isString
        map
        replaceStrings
        typeOf
        ;
      inherit (lib) concatMapStringsSep;

      mkIndent = level: concatStringsSep "" (map (_: " ") (lib.range 1 (level * indent)));
      escapeString =
        s:
        let
          escaped = replaceStrings [ "\\" "\"" "\n" "\r" "\t" ] [ "\\\\" "\\\"" "\\n" "\\r" "\\t" ] s;
        in
        "\"${escaped}\"";
      toJSONCImpl =
        level: v:
        let
          currentIndent = mkIndent level;
          nextIndent = mkIndent (level + 1);
          vType = typeOf v;
        in
        if vType == "null" then
          "null"
        else if isBool v then
          if v then "true" else "false"
        else if vType == "int" || vType == "float" then
          toString v
        else if isString v then
          escapeString v
        else if isList v then
          if v == [ ] then
            "[]"
          else
            "[\n${
              concatMapStringsSep ",\n" (item: "${nextIndent}${toJSONCImpl (level + 1) item}") v
            },\n${currentIndent}]"
        else if vType == "set" then
          let
            keys = attrNames v;
          in
          if keys == [ ] then
            "{}"
          else
            "{\n${
              concatMapStringsSep ",\n" (
                key: "${nextIndent}${escapeString key}: ${toJSONCImpl (level + 1) v.${key}}"
              ) keys
            },\n${currentIndent}}"
        else
          throw "toJSONC: unsupported type '${vType}'";
    in
    toJSONCImpl initialIndent value;
  userMetaIfExists =
    user:
    let
      userMetaFile = userMetaPath user;
    in
    optionalAttrs (userMetaFile != null) { userMeta = import userMetaFile; };
  userConfigPath = u: "${src}/home/${u}/configuration.nix";
  defaultUserModule = user: maybePath (userConfigPath user);
  kernel =
    system:
    let
      sysTup = split "-" system;
      k = elemAt sysTup (length sysTup - 1);
    in
    assert
      builtins.elem k [
        "darwin"
        "linux"
      ]
      || throw "unsupported kernel '${k}' from system '${system}' (expected darwin or linux)";
    k;
  homeDirBase =
    system:
    {
      darwin = "/Users";
      linux = "/home";
    }
    .${kernel system};
  srcDirBase =
    system:
    {
      darwin = "Development";
      linux = "src";
    }
    .${kernel system};
  ghRaw =
    {
      owner,
      repo,
      rev,
      path,
    }:
    "https://raw.githubusercontent.com/${owner}/${repo}/${rev}/${path}";
  # Shared module args for home-manager configurations
  mkHomeModuleArgs =
    { system, username }:
    {
      inherit
        inputs
        outputs
        src
        system
        username
        ;
      slib = outputs.lib;
    }
    // userMetaIfExists username;

  # External home-manager modules from flake inputs
  homeExternalModules = [
    inputs.nixvim.homeModules.nixvim
    inputs.sops-nix.homeManagerModules.sops
    inputs.stylix.homeModules.stylix
  ];

  # Shared module list for home-manager configurations
  mkHomeModules =
    {
      system,
      username ? null,
      userModule ? null,
      extraModules ? [ ],
      includeDefaultUserModule ? true,
    }:
    let
      resolvedUserModule =
        if userModule != null then
          userModule
        else if includeDefaultUserModule && username != null then
          defaultUserModule username
        else
          null;
    in
    homeExternalModules
    ++ [
      "${modulesPath}/home/theme.nix"
      "${modulesPath}/home/fonts.nix"
      "${modulesPath}/home/profiles.nix"
      "${modulesPath}/home/base.nix"
      "${modulesPath}/home/${kernel system}.nix"
    ]
    ++ optionals (resolvedUserModule != null) [ resolvedUserModule ]
    ++ toList extraModules;

  mkHome =
    {
      modules ? [ ],
      userModule ? null,
      includeDefaultUserModule ? true,
      extraSpecialArgs ? { },
      system,
      username,
      ...
    }:
    {
      inherit system;
      extraSpecialArgs =
        mkHomeModuleArgs { inherit system username; }
        // {
          pkgs = pkgsFor.${system};
        }
        // extraSpecialArgs;
      modules = mkHomeModules {
        inherit
          includeDefaultUserModule
          system
          userModule
          username
          ;
        extraModules = modules;
      };
    };
  mkSystem =
    {
      homeModules ? [ ],
      homeModulesByUser ? { },
      homeModuleArgsByUser ? { },
      includeDefaultUserModules ? true,
      extraSpecialArgs ? { },
      homeManagerExtraSpecialArgs ? { },
      system,
      systemModules ? [ ],
      users ? [ ],
      ...
    }:
    let
      hmEntryPoint =
        {
          darwin = "darwin";
          linux = "nixos";
        }
        .${kernel system};
      homeModulesList = toList homeModules;
      hasPerUserHomeModules = attrNames homeModulesByUser != [ ];
      hasPerUserHomeModuleArgs = attrNames homeModuleArgsByUser != [ ];
      enableHomeManager =
        users != [ ]
        && (
          includeDefaultUserModules
          || homeModulesList != [ ]
          || hasPerUserHomeModules
          || hasPerUserHomeModuleArgs
        );
    in
    {
      inherit system;
      specialArgs = {
        inherit
          inputs
          outputs
          src
          system
          ;
        primaryUser = if users == [ ] then null else elemAt users 0;
        slib = outputs.lib;
      }
      // extraSpecialArgs;
      modules = [
        "${modulesPath}/common.nix"
        "${modulesPath}/${kernel system}/base.nix"
        "${modulesPath}/${kernel system}/profiles.nix"
      ]
      ++ systemModules
      ++ optionals enableHomeManager [
        inputs.home-manager."${hmEntryPoint}Modules".home-manager
        {
          home-manager = {
            useGlobalPkgs = true;
            useUserPackages = true;
            extraSpecialArgs = {
              inherit
                inputs
                outputs
                src
                system
                ;
              slib = outputs.lib;
              pkgs = pkgsFor.${system};
            }
            // homeManagerExtraSpecialArgs;
            users = listToAttrs (
              map (user: {
                name = user;
                value = {
                  _module.args = {
                    username = user;
                  }
                  // userMetaIfExists user
                  // (homeModuleArgsByUser.${user} or { });
                  imports =
                    let
                      userModules = toList (homeModulesByUser.${user} or [ ]);
                    in
                    mkHomeModules {
                      inherit system;
                      includeDefaultUserModule = includeDefaultUserModules;
                      username = user;
                      extraModules = homeModulesList ++ userModules;
                    };
                };
              }) users
            );
          };
        }
      ];
    };

  mkSetOpencodeEnvModule = configName: _: {
    launchd.user.agents.set-opencode-env = {
      script = ''
        launchctl setenv OPENCODE_CONFIG "$HOME/.config/opencode/${configName}"
      '';
      serviceConfig = {
        Label = "com.nixcfg.set-opencode-env";
        RunAtLoad = true;
      };
    };
  };

  # Common base for all Darwin hosts — captures shared boilerplate so each
  # host file only declares its differences.  hostname is injected by
  # flakelight-darwin from the attrset key (i.e. the filename).
  mkDarwinHost =
    {
      user,
      system ? "aarch64-darwin",
      work ? false,
      brewAppsModule ? null,
      extraHomeModules ? [ ],
      homeModulesByUser ? { },
      homeModuleArgsByUser ? { },
      includeDefaultUserModule ? true,
      extraSpecialArgs ? { },
      homeManagerExtraSpecialArgs ? { },
      extraSystemModules ? [ ],
      enableRosettaBuilder ? !isCI,
    }:
    mkSystem {
      inherit
        extraSpecialArgs
        homeManagerExtraSpecialArgs
        homeModuleArgsByUser
        homeModulesByUser
        system
        ;
      includeDefaultUserModules = includeDefaultUserModule;
      users = [ user ];
      homeModules = toList extraHomeModules ++ optionals work [ (_: { profiles.work.enable = true; }) ];
      systemModules = [
        inputs.nix-homebrew.darwinModules.nix-homebrew
        "${modulesPath}/darwin/homebrew.nix"
        { nixcfg.darwin.homebrew.user = lib.mkDefault user; }
        # Linux builder for cross-platform Nix builds on Apple Silicon.
        # nix-rosetta-builder provides aarch64-linux and x86_64-linux builders
        # via Rosetta 2. Requires initial bootstrap with nix.linux-builder.
        # Skipped in CI — the builder VM image requires aarch64-linux to build
        # and GitHub Actions macOS runners lack a Linux builder.
      ]
      ++ optionals (brewAppsModule != null) [ brewAppsModule ]
      ++ optionals enableRosettaBuilder [
        inputs.nix-rosetta-builder.darwinModules.default
        { nix-rosetta-builder.onDemand = true; }
      ]
      ++ optionals work [ { profiles.work.enable = true; } ]
      ++ toList extraSystemModules;
    };
}
