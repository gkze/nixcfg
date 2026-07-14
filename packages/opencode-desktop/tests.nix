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

  prodIdentity = {
    opencodeChannel = "prod";
    appName = "OpenCode";
    appId = "ai.opencode.desktop";
    appProtocolScheme = "opencode";
  };

  devIdentity = {
    opencodeChannel = "dev";
    appName = "OpenCode Desktop Dev";
    appId = "ai.opencode.desktop.dev";
    appProtocolScheme = "opencode";
  };

  requiredDesktopWorkspacePaths = [
    "packages/codemode"
    "packages/http-recorder"
    "packages/plugin"
    "packages/protocol"
    "packages/schema"
    "packages/session-ui"
    "packages/tui"
  ];

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

  assertContainsAll =
    label: required: actual:
    let
      missing = builtins.filter (item: !(builtins.elem item actual)) required;
    in
    if missing == [ ] then true else throw "${label}: missing ${builtins.toJSON missing}";

  workspaceDependencyNames =
    manifest:
    builtins.concatLists (
      builtins.map
        (
          dependencies:
          builtins.filter (
            name:
            let
              version = builtins.getAttr name dependencies;
            in
            builtins.isString version && builtins.match "^workspace:.*" version != null
          ) (builtins.attrNames dependencies)
        )
        [
          (manifest.dependencies or { })
          (manifest.devDependencies or { })
          (manifest.optionalDependencies or { })
          (manifest.peerDependencies or { })
        ]
    );

  assertWorkspaceDependencyClosure =
    label: package:
    let
      workspacePaths = package.passthru.desktopWorkspacePaths;
      manifests = builtins.map (
        workspacePath:
        builtins.fromJSON (builtins.readFile (package.src + "/${workspacePath}/package.json"))
      ) workspacePaths;
      includedNames = builtins.map (
        manifest: manifest.name or (throw "${label}: workspace manifest has no name")
      ) manifests;
      requiredNames = builtins.concatLists (builtins.map workspaceDependencyNames manifests);
      missing = builtins.filter (name: !(builtins.elem name includedNames)) requiredNames;
    in
    if missing == [ ] then
      true
    else
      throw "${label}: workspace dependency closure is missing ${builtins.toJSON missing}";

  perSystemChecks = builtins.concatLists (
    builtins.map (
      system:
      let
        prod = packageFor system "opencode-desktop";
        dev = packageFor system "opencode-desktop-dev";
      in
      [
        (assertEq "prod channel (${system})" prodIdentity.opencodeChannel prod.passthru.opencodeChannel)
        (assertEq "prod appName (${system})" prodIdentity.appName prod.passthru.appName)
        (assertEq "prod appId (${system})" prodIdentity.appId prod.passthru.appId)
        (assertEq "prod protocol scheme (${system})" prodIdentity.appProtocolScheme
          prod.passthru.appProtocolScheme
        )
        (assertEq "prod runtime version (${system})" prod.passthru.electronVersion
          prod.passthru.electronRuntimeVersion
        )
        (assertStorePath "prod electronDist (${system})" prod.passthru.electronDist)
        (assertStorePath "prod drvPath (${system})" prod.drvPath)

        (assertEq "dev channel (${system})" devIdentity.opencodeChannel dev.passthru.opencodeChannel)
        (assertEq "dev appName (${system})" devIdentity.appName dev.passthru.appName)
        (assertEq "dev appId (${system})" devIdentity.appId dev.passthru.appId)
        (assertEq "dev protocol scheme (${system})" devIdentity.appProtocolScheme
          dev.passthru.appProtocolScheme
        )
        (assertEq "dev runtime version (${system})" dev.passthru.electronVersion
          dev.passthru.electronRuntimeVersion
        )
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
      (packageFor "aarch64-darwin" "opencode-desktop").meta.platforms
    )
    (assertEq "dev meta.platforms" supportedPlatforms
      (packageFor "aarch64-darwin" "opencode-desktop-dev").meta.platforms
    )
    (assertContainsAll "desktop workspace filters include transitive workspaces"
      requiredDesktopWorkspacePaths
      (packageFor "aarch64-darwin" "opencode-desktop").passthru.desktopWorkspacePaths
    )
    (assertWorkspaceDependencyClosure "desktop workspace filters" (
      packageFor "aarch64-darwin" "opencode-desktop"
    ))
  ]
  ++ perSystemChecks;
in
builtins.deepSeq checks true
