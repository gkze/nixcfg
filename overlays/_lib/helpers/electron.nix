{
  inputs,
  final,
  slib,
  ...
}:
let
  mkGenericElectron = final.callPackage (
    inputs.nixpkgs + "/pkgs/development/tools/electron/binary/generic.nix"
  ) { };

  systemPolicy = builtins.fromJSON (builtins.readFile ../../../lib/system-policy.json);
  rawArtifactTags =
    assert systemPolicy.schemaVersion == 1;
    systemPolicy.electronArtifacts or (throw "nixcfgElectron: system policy missing electronArtifacts");
  artifactTags = builtins.mapAttrs (
    system: tag:
    if builtins.isString tag && builtins.match "^(darwin|linux)-(arm64|x64)$" tag != null then
      tag
    else
      throw "nixcfgElectron: system policy ${system} has invalid Electron artifact tag"
  ) rawArtifactTags;
  supportedArtifactSystems = builtins.attrNames artifactTags;
  fakeHashMode = slib.fakeHashMode or false;
  inventory = slib.sourceEntry "electron-runtimes";
  inventoryUrls =
    if builtins.isAttrs (inventory.urls or null) then
      inventory.urls
    else
      throw "nixcfgElectron: runtime inventory urls must be an attribute set";
  expectedInventoryVersion = "inventory-v1";
  requiredArtifacts = [ "headers" ] ++ supportedArtifactSystems;

  artifactUrl =
    version: artifact:
    if artifact == "headers" then
      "https://artifacts.electronjs.org/headers/dist/v${version}/node-v${version}-headers.tar.gz"
    else
      let
        tag =
          artifactTags.${artifact} or (throw "nixcfgElectron: unsupported runtime artifact ${artifact}");
      in
      "https://github.com/electron/electron/releases/download/v${version}/electron-v${version}-${tag}.zip";

  parseRecord =
    record:
    let
      key = record.platform or (throw "nixcfgElectron: runtime hash record missing inventory key");
      match = builtins.match "^([0-9]+\\.[0-9]+\\.[0-9]+):([A-Za-z0-9_-]+)$" key;
    in
    if match == null then
      throw "nixcfgElectron: malformed runtime inventory key ${key}"
    else
      let
        version = builtins.elemAt match 0;
        artifact = builtins.elemAt match 1;
        hashType = record.hashType or (throw "nixcfgElectron: runtime record ${key} missing hashType");
        hash = record.hash or (throw "nixcfgElectron: runtime record ${key} missing hash");
        url = inventoryUrls.${key} or (throw "nixcfgElectron: runtime record ${key} missing url");
      in
      if hashType != "sha256" then
        throw "nixcfgElectron: runtime record ${key} must use sha256"
      else if builtins.match "^sha256-[A-Za-z0-9+/]+=*$" hash == null then
        throw "nixcfgElectron: runtime record ${key} has a malformed SRI hash"
      else if !builtins.isString url then
        throw "nixcfgElectron: runtime record ${key} has a non-string url"
      else
        {
          inherit
            version
            artifact
            hash
            url
            ;
        };

  keepRecord = record: !fakeHashMode || builtins.elem record.artifact requiredArtifacts;

  validateRecord =
    record:
    let
      expectedUrl =
        if builtins.elem record.artifact requiredArtifacts then
          artifactUrl record.version record.artifact
        else
          null;
    in
    if expectedUrl != null && record.url != expectedUrl then
      throw "nixcfgElectron: runtime record ${record.version}:${record.artifact} URL does not match system policy"
    else
      record;

  insertRecord =
    records: record:
    let
      versionArtifacts = records.${record.version} or { };
    in
    if builtins.hasAttr record.artifact versionArtifacts then
      throw "nixcfgElectron: duplicate ${record.version}:${record.artifact} runtime record"
    else
      records
      // {
        "${record.version}" = versionArtifacts // {
          "${record.artifact}" = {
            inherit (record) hash url;
          };
        };
      };

  validateVersion =
    version: versionHashes:
    let
      present = builtins.attrNames versionHashes;
      missing = final.lib.subtractLists present requiredArtifacts;
      unexpected = final.lib.subtractLists requiredArtifacts present;
    in
    if missing != [ ] || unexpected != [ ] then
      throw "nixcfgElectron: incomplete runtime ${version}; missing ${final.lib.concatStringsSep "," missing}; unexpected ${final.lib.concatStringsSep "," unexpected}"
    else
      versionHashes;

  parsedRecords =
    if (inventory.version or null) != expectedInventoryVersion then
      throw "nixcfgElectron: unsupported runtime inventory version ${
        toString (inventory.version or null)
      }"
    else if !(builtins.isList (inventory.hashes or null)) then
      throw "nixcfgElectron: runtime inventory hashes must be a list"
    else
      map parseRecord inventory.hashes;
  records = map validateRecord (final.lib.filter keepRecord parsedRecords);
  rawArtifacts = builtins.foldl' insertRecord { } records;
  sourceElectronVersion =
    name: source:
    let
      version = source.electronVersion or null;
      legacyVersion = (source.pins or { }).electronVersion or null;
    in
    if legacyVersion != null then
      throw "nixcfgElectron: source override ${name} still uses legacy pins.electronVersion metadata"
    else if version == null then
      null
    else if builtins.match "^[0-9]+\\.[0-9]+\\.[0-9]+$" version == null then
      throw "nixcfgElectron: source override ${name} has non-exact electronVersion ${version}"
    else
      version;
  candidateVersions = final.lib.unique (
    final.lib.filter (version: version != null) (
      final.lib.mapAttrsToList sourceElectronVersion (
        if fakeHashMode then slib.sources else slib.sourceOverrides or { }
      )
    )
  );
  syntheticArtifacts = final.lib.genAttrs candidateVersions (
    version:
    final.lib.genAttrs requiredArtifacts (artifact: {
      hash = final.lib.fakeHash;
      url = artifactUrl version artifact;
    })
  );
  effectiveArtifacts = builtins.mapAttrs (
    version: versionArtifacts: (syntheticArtifacts.${version} or { }) // versionArtifacts
  ) (syntheticArtifacts // rawArtifacts);
  validatedArtifacts =
    if effectiveArtifacts == { } then
      throw "nixcfgElectron: runtime inventory must not be empty"
    else
      builtins.mapAttrs validateVersion effectiveArtifacts;
  artifacts = builtins.deepSeq validatedArtifacts validatedArtifacts;
  hashes = builtins.mapAttrs (
    _version: versionArtifacts: builtins.mapAttrs (_artifact: artifact: artifact.hash) versionArtifacts
  ) artifacts;
  allVersions = builtins.sort final.lib.versionOlder (builtins.attrNames artifacts);

  mkElectron =
    version: versionArtifacts:
    let
      genericHashes = builtins.mapAttrs (_artifact: artifact: artifact.hash) versionArtifacts;
      genericRuntime = mkGenericElectron version genericHashes;
      system = final.stdenv.hostPlatform.system;
      binary =
        versionArtifacts.${system}
          or (throw "nixcfgElectron: runtime ${version} missing build artifact for ${system}");
      inherit (versionArtifacts) headers;
    in
    genericRuntime.overrideAttrs (previous: {
      src = final.fetchurl {
        inherit (binary) url hash;
      };
      passthru = previous.passthru // {
        headers = final.fetchzip {
          name = "electron-${version}-headers";
          inherit (headers) url hash;
        };
      };
    });
  runtimes = builtins.mapAttrs mkElectron artifacts;

  runtimeFor =
    version:
    runtimes.${version} or (throw "nixcfgElectron: missing packaged Electron runtime for ${version}");

  sourceBuildFor =
    version:
    let
      runtime = runtimeFor version;
      exactRuntime =
        if runtime.version == version then
          runtime
        else
          throw "nixcfgElectron: runtime ${version} resolved Electron ${runtime.version}";
    in
    {
      inherit version;
      runtime = exactRuntime;
      runtimeVersion = exactRuntime.version;
      inherit (exactRuntime.passthru) headers;
      inherit (exactRuntime.passthru) dist;
      commonEnv = {
        ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
        npm_config_runtime = "electron";
        npm_config_target = version;
        npm_config_nodedir = toString exactRuntime.passthru.headers;
      };
      copyDist = ''
        electronDistDir="$PWD/electron-dist"
        mkdir -p "$electronDistDir"
        cp -R ${exactRuntime.passthru.dist}/. "$electronDistDir"/
        chmod -R u+w "$electronDistDir"
      '';
      electronBuilderConfigFlags = ''
        -c.electronDist="$electronDistDir" \
        -c.electronVersion=${final.lib.escapeShellArg exactRuntime.version} \
      '';
    };
in
{
  nixcfgElectron = {
    inherit
      allVersions
      hashes
      runtimeFor
      runtimes
      sourceBuildFor
      ;

    versionsForSystem = _system: allVersions;
  };
}
