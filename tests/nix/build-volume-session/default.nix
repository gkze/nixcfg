{ self }:
let
  inherit (self.inputs.nixpkgs) lib;
  volume = import ../../../darwin/george/build-volume-identity.nix;
  env = import ../../../darwin/george/build-volume-env.nix;
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  argus = self.darwinConfigurations.argus.config;
  home = argus.home-manager.users.george;
  loginSetter = argus.launchd.user.agents.build-volume-env;
  expectedLoginScript = ''
    set -e
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (
        name: value: "/bin/launchctl setenv ${lib.escapeShellArg name} ${lib.escapeShellArg value}"
      ) env
    )}
  '';
  expectedMigration = ''
    buildVolumeBunInstall=${lib.escapeShellArg env.BUN_INSTALL}
    ${builtins.readFile ../../../darwin/george/build-volume-bun-migration.sh}
  '';

  argusChecks = [
    (assertEq "Argus Spotlight excludes the shared Build volume" [
      volume.name
    ] argus.darwinDefaults.spotlight.unindexedVolumes)
    (assertEq "Argus HM Build environment" env (builtins.intersectAttrs env home.home.sessionVariables))
    (assertEq "Argus launchd Build environment" env (
      builtins.intersectAttrs env argus.launchd.user.envVariables
    ))
    (assertEq "Argus login setter label" "com.gkze.build-volume-env" loginSetter.serviceConfig.Label)
    (assertEq "Argus login setter RunAtLoad" true loginSetter.serviceConfig.RunAtLoad)
    (assertEq "Argus login setter script" expectedLoginScript loginSetter.script)
    (assertEq "Argus Bun link" (home.lib.file.mkOutOfStoreSymlink env.BUN_INSTALL)
      home.home.file.".bun".source
    )
    (assertEq "Argus cache placeholder disabled" false
      home.home.file."/Volumes/Build/cache/.keep".enable
    )
    (assertEq "Argus Bun migration ordering" [
      "writeBoundary"
    ] home.home.activation.buildVolumeBunMigration.before)
    (assertEq "Argus Bun migration script" expectedMigration
      home.home.activation.buildVolumeBunMigration.data
    )
  ];

  siblingChecks =
    builtins.concatMap
      (
        hostName:
        let
          systemConfig = self.darwinConfigurations.${hostName}.config;
          homeConfig = systemConfig.home-manager.users.george;
        in
        [
          (assertEq "${hostName} has no launchd Bun root" false (
            builtins.hasAttr "BUN_INSTALL" systemConfig.launchd.user.envVariables
          ))
          (assertEq "${hostName} has no HM Bun root" false (
            builtins.hasAttr "BUN_INSTALL" homeConfig.home.sessionVariables
          ))
          (assertEq "${hostName} has no Build login setter" false (
            builtins.hasAttr "build-volume-env" systemConfig.launchd.user.agents
          ))
          (assertEq "${hostName} has no Bun link" false (builtins.hasAttr ".bun" homeConfig.home.file))
          (assertEq "${hostName} has no Bun migration" false (
            builtins.hasAttr "buildVolumeBunMigration" homeConfig.home.activation
          ))
          (assertEq "${hostName} keeps the default cache" "/Users/george/.cache" homeConfig.xdg.cacheHome)
        ]
      )
      [
        "rocinante"
        "zeus"
      ];
in
builtins.deepSeq (argusChecks ++ siblingChecks) true
