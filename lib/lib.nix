{
  evaluationContext ? { },
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
    fromJSON
    hasAttr
    isList
    length
    listToAttrs
    map
    pathExists
    readFile
    split
    ;
  inherit (lib.lists) findFirst optionals;
  inherit (lib.attrsets) optionalAttrs;

  maybePath = p: if pathExists p then p else null;
  discovery = import ./discovery.nix;
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
      sourceFiles = discovery.discoverSidecarEntries {
        root = dir;
        fileName = "sources.json";
      };
    in
    builtins.mapAttrs (_name: path: fromJSON (readFile path)) sourceFiles.entries;
  packageSources = scanSourcesIn (src + "/packages") // scanSourcesIn (src + "/overlays");
  updateSourceOverrides = evaluationContext.sourceOverrides or { };
  # Select exactly one entry by hash type and optional platform/URL identity.
  # An omitted platform or URL does not filter it. `platform = null` matches an
  # explicit null, while `unqualified = true` requires the attribute to be absent.
  # Missing entries stay nullable so callers retain their own fallback policy.
  selectSourceHashEntry =
    entries: hashType: selection:
    let
      matches = builtins.filter (
        entry:
        entry.hashType == hashType
        && (
          if selection.unqualified or false then
            !(entry ? platform)
          else if selection ? platform then
            (entry.platform or null) == selection.platform
          else
            true
        )
        && (!(selection ? url) || (entry.url or null) == selection.url)
      ) entries;
      matchCount = length matches;
    in
    if matchCount == 1 then
      elemAt matches 0
    else if matchCount == 0 then
      null
    else
      throw "selectSourceHashEntry: expected exactly one ${hashType} entry for ${builtins.toJSON selection}, found ${toString matchCount}";

  resolveSourceHashEntry =
    sourceEntry: fakeHashMode: name: hashType: selection:
    let
      platformSpecific = selection ? platform;
    in
    if fakeHashMode then
      {
        hash = lib.fakeHash;
        inherit hashType;
      }
      // optionalAttrs platformSpecific { inherit (selection) platform; }
      // optionalAttrs (!platformSpecific) { gitDep = "fake-dep"; }
    else
      let
        matchEntry = selectSourceHashEntry ((sourceEntry name).hashes or [ ]) hashType selection;
      in
      if matchEntry == null then
        throw "sources.json for '${name}' missing ${hashType}${
          if platformSpecific then " on ${selection.platform}" else ""
        }"
      else
        matchEntry;
in
rec {
  inherit (crate2nixTauri)
    mkCrate2nixTauriEnvOverride
    mkCrate2nixTauriOverrides
    mkCrate2nixTauriUtilsOverride
    tauriPluginEnvCrateNames
    ;
  inherit (rustyV8) mkRustyV8Build mkRustyV8PrebuiltArtifacts;
  inherit modulesPath selectSourceHashEntry;
  flakeLock = (fromJSON (readFile (src + "/flake.lock"))).nodes;
  # Update tooling may override selected sources entries during evaluation
  # without mutating tracked sources.json files.
  sourceOverrides = updateSourceOverrides;
  sources = packageSources // sourceOverrides;

  # In fake-hash mode, all sourceHash* functions return lib.fakeHash instead
  # of reading from sources.json. This lets the update script evaluate the
  # overlay derivations with placeholder hashes to trigger hash-mismatch errors
  # from which the correct hashes are extracted — without duplicating any
  # derivation logic.
  fakeHashMode = evaluationContext.fakeHashes or false;

  sourceEntry =
    name: if hasAttr name sources then sources.${name} else throw "sources.json missing for '${name}'";

  # Find hash entry matching hashType and optionally platform.
  sourceHashEntry =
    name: hashType:
    resolveSourceHashEntry sourceEntry fakeHashMode name hashType { unqualified = true; };

  sourceHashEntryForPlatform =
    name: hashType: platform:
    resolveSourceHashEntry sourceEntry fakeHashMode name hashType { inherit platform; };

  sourceHash = name: hashType: (sourceHashEntry name hashType).hash;

  # Get hash for a specific platform
  sourceHashForPlatform =
    name: hashType: platform:
    (sourceHashEntryForPlatform name hashType platform).hash;
  sourceUrl =
    name: hashType:
    let
      # The fake-hash contract covers hashes only: the update probe still needs
      # the real download URL, and the fake record carries none. Resolve the
      # entry with fake mode off so both modes report the same url.
      entry = resolveSourceHashEntry sourceEntry false name hashType { unqualified = true; };
    in
    entry.url or (throw "sources.json for '${name}' missing url for ${hashType}");

  normalizeName = s: builtins.replaceStrings [ "." "_" ] [ "-" "-" ] s;

  # Strip only recognized leading version prefixes from flake refs.
  stripVersionPrefix =
    s:
    let
      prefix = findFirst (candidate: lib.hasPrefix candidate s) null [
        "rust-v"
        "desktop-v"
        "v"
      ];
    in
    if prefix == null then s else lib.removePrefix prefix s;

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
        typeOf
        ;
      inherit (lib) concatMapStringsSep;

      mkIndent = level: concatStringsSep "" (map (_: " ") (lib.range 1 (level * indent)));
      escapeString = builtins.toJSON;
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
      # The `pkgs` argument of `homeManagerConfiguration`: the configured
      # package set from `pkgsFor` is the only Nixpkgs instance in play.
      pkgs = pkgsFor.${system};
      extraSpecialArgs = mkHomeModuleArgs { inherit system username; } // extraSpecialArgs;
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
      primaryUser = if users != [ ] then elemAt users 0 else null;
      userContract =
        if users == [ ] && kernel system == "darwin" then
          throw "mkSystem: Darwin systems require at least one user"
        else
          null;
      enableHomeManager =
        users != [ ]
        && (
          includeDefaultUserModules
          || homeModulesList != [ ]
          || hasPerUserHomeModules
          || hasPerUserHomeModuleArgs
        );
    in
    builtins.seq userContract {
      inherit system;
      specialArgs = {
        inherit
          inputs
          outputs
          src
          system
          ;
        inherit primaryUser;
        slib = outputs.lib;
      }
      // extraSpecialArgs;
      modules = [
        # One Nixpkgs instance per system: the system evaluation and Home
        # Manager (through useGlobalPkgs) both use the already configured
        # `pkgsFor` set. Its creator owns `config` and overlays; the
        # nix-darwin and NixOS modules reject `nixpkgs.config` next to it.
        { nixpkgs.pkgs = lib.mkDefault pkgsFor.${system}; }
        "${modulesPath}/common.nix"
        "${modulesPath}/${kernel system}/base.nix"
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
              # The same instance as `nixpkgs.pkgs` above. Some modules use
              # `pkgs` to decide their own shape, so it must be a special
              # argument; resolving it through `_module.args` recurses.
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

  # Compatibility entrypoint for consumers without the Home Manager integration.
  # When Home Manager selects a profile, its resolved path is authoritative.
  mkSetOpencodeEnvModule =
    configName:
    moduleArgs@{
      config,
      ...
    }:
    {
      launchd.user.agents.set-opencode-env = {
        script =
          let
            # Named module arguments are required even with a function default.
            primaryUser = moduleArgs.primaryUser or null;
            selectedPath =
              if primaryUser == null then
                null
              else
                lib.attrByPath [
                  "home-manager"
                  "users"
                  primaryUser
                  "home"
                  "sessionVariables"
                  "OPENCODE_CONFIG"
                ] null config;
            path =
              if selectedPath == null then
                ''"$HOME"/${lib.escapeShellArg ".config/opencode/${configName}"}''
              else
                lib.escapeShellArg selectedPath;
          in
          ''
            launchctl setenv OPENCODE_CONFIG ${path}
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
      brewAppsModule ? null,
      extraHomeModules ? [ ],
      homeModulesByUser ? { },
      homeModuleArgsByUser ? { },
      includeDefaultUserModule ? true,
      extraSpecialArgs ? { },
      homeManagerExtraSpecialArgs ? { },
      extraSystemModules ? [ ],
      enableRosettaBuilder ? true,
      rosettaBuilderMemory ? null,
      rosettaBuilderLingerMinutes ? null,
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
      homeModules = toList extraHomeModules;
      systemModules = [
        inputs.nix-homebrew.darwinModules.nix-homebrew
        "${modulesPath}/darwin/homebrew.nix"
        { nixcfg.darwin.homebrew.user = lib.mkDefault user; }
        # Linux builder for cross-platform Nix builds on Apple Silicon.
        # nix-rosetta-builder provides aarch64-linux and x86_64-linux builders
        # via Rosetta 2. Requires initial bootstrap with nix.linux-builder.
        # Native Linux CI builds the image before Darwin consumes it.
      ]
      ++ optionals (brewAppsModule != null) [ brewAppsModule ]
      ++ optionals enableRosettaBuilder (
        [
          inputs.nix-rosetta-builder.darwinModules.default
          {
            nix-rosetta-builder.onDemand = true;
            nix-rosetta-builder.potentiallyInsecureExtraNixosModule.nixpkgs.overlays = [
              (import ./rosetta-builder-image.nix { inherit lib; })
            ];
          }
        ]
        ++ optionals (rosettaBuilderMemory != null) [
          { nix-rosetta-builder.memory = rosettaBuilderMemory; }
        ]
        ++ optionals (rosettaBuilderLingerMinutes != null) [
          { nix-rosetta-builder.onDemandLingerMinutes = rosettaBuilderLingerMinutes; }
        ]
      )
      ++ toList extraSystemModules;
    };
}
