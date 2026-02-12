{
  inputs,
  final,
  prev,
  slib,
  ...
}:
let
  inherit (final) craneLib;
  version = slib.getFlakeVersion "codex";
  src = "${inputs.codex}/codex-rs";
  commonArgs = {
    inherit src version;
    pname = "codex";
    strictDeps = true;
    cargoLock = "${inputs.codex}/codex-rs/Cargo.lock";
    # Use --offline instead of --locked to avoid checksum mismatch errors
    # for git dependencies (runfiles from rules_rust)
    cargoExtraArgs = "--offline";

    nativeBuildInputs = [
      prev.clang
      prev.cmake
      prev.gitMinimal
      prev.installShellFiles
      prev.makeBinaryWrapper
      prev.pkg-config
      prev.ninja
      prev.go
      prev.perl
    ];

    buildInputs = [
      prev.libclang
      prev.openssl
    ]
    ++ prev.lib.optionals prev.stdenv.hostPlatform.isDarwin [ prev.apple-sdk_15 ];

    LIBCLANG_PATH = "${prev.lib.getLib prev.libclang}/lib";
    NIX_CFLAGS_COMPILE = toString (
      prev.lib.optionals prev.stdenv.cc.isClang [
        "-Wno-error=character-conversion"
      ]
    );
  };
  cargoArtifacts = craneLib.buildDepsOnly commonArgs;
in
{
  codex = craneLib.buildPackage (
    commonArgs
    // {
      inherit cargoArtifacts;
      doCheck = false;

      postInstall = ''
        installShellCompletion --cmd codex \
          --bash <($out/bin/codex completion bash) \
          --fish <($out/bin/codex completion fish) \
          --zsh <($out/bin/codex completion zsh)
      '';

      postFixup = ''
        wrapProgram $out/bin/codex --prefix PATH : ${prev.lib.makeBinPath [ prev.ripgrep ]}
      '';

      meta = with prev.lib; {
        description = "Lightweight coding agent that runs in your terminal";
        homepage = "https://github.com/openai/codex";
        license = licenses.asl20;
        mainProgram = "codex";
        platforms = platforms.unix;
      };
    }
  );
}
