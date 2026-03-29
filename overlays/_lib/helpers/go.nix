{
  final,
  inputs,
  prev,
  slib,
  ...
}:
rec {
  craneLib = inputs.crane.mkLib final;

  mkGoCliPackage =
    {
      pname,
      input,
      subPackages,
      cmdName ? pname,
      version ? null,
      meta ? { },
      doCheck ? true,
      go ? prev.go,
      ...
    }@args:
    let
      flakeRef = slib.flakeLock.${pname};
      finalVersion =
        if version != null then version else slib.stripVersionPrefix (flakeRef.original.ref or "");
      buildGoModule =
        if go == prev.go then prev.buildGoModule else prev.buildGoModule.override { inherit go; };
    in
    buildGoModule (
      {
        inherit pname subPackages;
        version = finalVersion;
        src = input;
        vendorHash = slib.sourceHash pname "vendorHash";
        inherit doCheck;
        nativeBuildInputs = [ prev.installShellFiles ];
        postInstall = ''
          installShellCompletion --cmd ${cmdName} \
            --bash <($out/bin/${cmdName} completion bash) \
            --fish <($out/bin/${cmdName} completion fish) \
            --zsh <($out/bin/${cmdName} completion zsh)
        '';
        meta = {
          mainProgram = cmdName;
        }
        // meta;
      }
      // (builtins.removeAttrs args [
        "pname"
        "input"
        "subPackages"
        "cmdName"
        "version"
        "meta"
        "doCheck"
        "go"
      ])
    );

  mkGoCli = import ../../../lib/go-cli-package.nix {
    inherit inputs;
    inherit (prev) lib;
    inherit mkGoCliPackage;
  };
}
