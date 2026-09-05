{
  buildGoModule,
  fetchFromGitHub,
  lib,
  outputs,
  selfSource,
  ...
}:
let
  pname = "baseten";
  inherit (selfSource) commit version;
in
buildGoModule {
  inherit pname version;

  src = fetchFromGitHub {
    owner = "basetenlabs";
    repo = "baseten-cli";
    rev = commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  vendorHash = outputs.lib.sourceHash pname "vendorHash";
  subPackages = [ "cmd/baseten" ];

  env.CGO_ENABLED = 0;
  ldflags = [
    "-s"
    "-w"
    "-X=github.com/basetenlabs/baseten-cli/internal/cmd.Version=${version}"
  ];

  postInstall = ''
    install -Dm0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
    test "$("$out/bin/baseten" --version)" = "${version}"
  '';

  meta = with lib; {
    description = "Command-line interface for managing Baseten resources";
    homepage = "https://github.com/basetenlabs/baseten-cli";
    changelog = "https://github.com/basetenlabs/baseten-cli/releases/tag/v${version}";
    license = licenses.mit;
    mainProgram = "baseten";
    platforms = [
      "aarch64-darwin"
      "x86_64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ];
    sourceProvenance = with sourceTypes; [ fromSource ];
  };
}
