{
  self,
}:
let
  exportedSystems = [
    "aarch64-darwin"
    "aarch64-linux"
    "x86_64-linux"
  ];

  supportedPlatforms = [
    "aarch64-darwin"
    "x86_64-darwin"
    "aarch64-linux"
    "x86_64-linux"
  ];

  majorOf = version: builtins.head (builtins.splitVersion version);

  prodIdentity = {
    opencodeChannel = "prod";
    appName = "OpenCode";
    appId = "ai.opencode.desktop";
    appProtocolScheme = "opencode";
  };

  devIdentity = {
    opencodeChannel = "dev";
    appName = "OpenCode Dev";
    appId = "ai.opencode.desktop.dev";
    appProtocolScheme = "opencode";
  };

  packageFor = system: name: builtins.getAttr name (builtins.getAttr system self.packages);

  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  assertStorePath =
    label: value:
    if builtins.match "^/nix/store/.*" value != null then
      true
    else
      throw "${label}: expected a /nix/store path, got ${builtins.toJSON value}";

  perSystemChecks = builtins.concatLists (
    builtins.map (
      system:
      let
        prod = packageFor system "opencode-desktop-electron";
        dev = packageFor system "opencode-desktop-electron-dev";
      in
      [
        (assertEq "prod channel (${system})" prodIdentity.opencodeChannel prod.passthru.opencodeChannel)
        (assertEq "prod appName (${system})" prodIdentity.appName prod.passthru.appName)
        (assertEq "prod appId (${system})" prodIdentity.appId prod.passthru.appId)
        (assertEq "prod protocol scheme (${system})" prodIdentity.appProtocolScheme
          prod.passthru.appProtocolScheme
        )
        (assertEq "prod runtime major (${system})" (majorOf prod.passthru.electronVersion) (
          majorOf prod.passthru.electronRuntimeVersion
        ))
        (assertStorePath "prod electronDist (${system})" prod.passthru.electronDist)
        (assertStorePath "prod drvPath (${system})" prod.drvPath)

        (assertEq "dev channel (${system})" devIdentity.opencodeChannel dev.passthru.opencodeChannel)
        (assertEq "dev appName (${system})" devIdentity.appName dev.passthru.appName)
        (assertEq "dev appId (${system})" devIdentity.appId dev.passthru.appId)
        (assertEq "dev protocol scheme (${system})" devIdentity.appProtocolScheme
          dev.passthru.appProtocolScheme
        )
        (assertEq "dev runtime major (${system})" (majorOf dev.passthru.electronVersion) (
          majorOf dev.passthru.electronRuntimeVersion
        ))
        (assertEq "dev runtime version matches prod (${system})" prod.passthru.electronRuntimeVersion
          dev.passthru.electronRuntimeVersion
        )
        (assertStorePath "dev electronDist (${system})" dev.passthru.electronDist)
        (assertStorePath "dev drvPath (${system})" dev.drvPath)
      ]
    ) exportedSystems
  );

  checks = [
    (assertEq "prod meta.platforms" supportedPlatforms
      (packageFor "aarch64-darwin" "opencode-desktop-electron").meta.platforms
    )
    (assertEq "dev meta.platforms" supportedPlatforms
      (packageFor "aarch64-darwin" "opencode-desktop-electron-dev").meta.platforms
    )
  ]
  ++ perSystemChecks;
in
builtins.deepSeq checks true
