{ self }:
let
  inherit (self.inputs.nixpkgs) lib;
  volume = import ../../../darwin/george/build-volume-identity.nix;
  env = import ../../../darwin/george/build-volume-env.nix;
  inherit (import ../lib.nix) assertEq;

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

  # Every George host provisions and consumes the same Build volume, so the
  # invariant is checked per host instead of against one reference machine.
  hostChecks =
    hostName:
    let
      system = self.darwinConfigurations.${hostName}.config;
      home = system.home-manager.users.george;
      loginSetter = system.launchd.user.agents.build-volume-env;
      attachAgent = system.launchd.user.agents.build-volume.serviceConfig;
      # Compared as plain text: string matching rejects store-path context.
      attachProgram = builtins.unsafeDiscardStringContext (builtins.head attachAgent.ProgramArguments);
    in
    [
      (assertEq "${hostName} Spotlight excludes the Build volume" [
        volume.name
      ] system.darwinDefaults.spotlight.unindexedVolumes)
      (assertEq "${hostName} HM Build environment" env (
        builtins.intersectAttrs env home.home.sessionVariables
      ))
      (assertEq "${hostName} launchd Build environment" env (
        builtins.intersectAttrs env system.launchd.user.envVariables
      ))
      (assertEq "${hostName} login setter label" "com.gkze.build-volume-env"
        loginSetter.serviceConfig.Label
      )
      (assertEq "${hostName} login setter RunAtLoad" true loginSetter.serviceConfig.RunAtLoad)
      (assertEq "${hostName} login setter script" expectedLoginScript loginSetter.script)
      (assertEq "${hostName} attach agent label" "com.gkze.build-volume" attachAgent.Label)
      (assertEq "${hostName} attach agent RunAtLoad" true attachAgent.RunAtLoad)
      # Activation must attach with the same program the login agent runs, so
      # the first switch on a new host provisions the volume before Home
      # Manager and Homebrew consume it.
      (assertEq "${hostName} activation attaches the Build volume" true (
        lib.hasInfix attachProgram (
          builtins.unsafeDiscardStringContext system.system.activationScripts.preActivation.text
        )
      ))
      (assertEq "${hostName} trusts george for per-invocation builders" true (
        builtins.elem "george" system.nix.settings.trusted-users
      ))
      (assertEq "${hostName} Bun link" (home.lib.file.mkOutOfStoreSymlink env.BUN_INSTALL)
        home.home.file.".bun".source
      )
      (assertEq "${hostName} cache placeholder disabled" false
        home.home.file."${env.XDG_CACHE_HOME}/.keep".enable
      )
      (assertEq "${hostName} Bun migration ordering" [
        "writeBoundary"
      ] home.home.activation.buildVolumeBunMigration.before)
      (assertEq "${hostName} Bun migration script" expectedMigration
        home.home.activation.buildVolumeBunMigration.data
      )
    ];
in
builtins.deepSeq (builtins.concatMap hostChecks (builtins.attrNames self.darwinConfigurations)) true
