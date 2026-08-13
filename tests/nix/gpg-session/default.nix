{
  self,
}:
let
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  checksFor =
    hostName:
    let
      systemConfig = self.darwinConfigurations.${hostName}.config;
      homeConfig = systemConfig.home-manager.users.george;
      gpgHome = homeConfig.programs.gpg.homedir;
      inherit (homeConfig.launchd) agents;
      loginSetter = agents.gpg-home.config;
    in
    [
      (assertEq "${hostName} shell GPG home" gpgHome homeConfig.home.sessionVariables.GNUPGHOME)
      (assertEq "${hostName} launchd GPG home" gpgHome systemConfig.launchd.user.envVariables.GNUPGHOME)
      (assertEq "${hostName} login setter RunAtLoad" true loginSetter.RunAtLoad)
      (assertEq "${hostName} login setter command" [
        "/bin/launchctl"
        "setenv"
        "GNUPGHOME"
        gpgHome
      ] loginSetter.ProgramArguments)
      (assertEq "${hostName} managed GPG agent" false (builtins.hasAttr "gpg-agent" agents))
      (assertEq "${hostName} legacy GPG home compatibility" true (
        builtins.hasAttr ".gnupg" homeConfig.home.file
      ))
      (assertEq "${hostName} legacy GPG home adoption" true homeConfig.home.file.".gnupg".force)
    ];

  checks = builtins.concatMap checksFor [
    "argus"
    "rocinante"
  ];
in
builtins.deepSeq checks true
