{
  stdenvNoCC,
  deno,
  cacert,
  installShellFiles,
  inputs,
  outputs,
  lib,
  stdenv,
  ...
}:
let
  slib = outputs.lib;
  version = slib.getFlakeVersion "linear-cli";
  inherit (stdenv.hostPlatform) system;

  denoDeps = stdenvNoCC.mkDerivation {
    pname = "linear-cli-deps";
    inherit version;
    src = inputs.linear-cli;
    nativeBuildInputs = [
      deno
      cacert
    ];
    outputHashAlgo = "sha256";
    outputHashMode = "recursive";
    outputHash = slib.sourceHashForPlatform "linear-cli" "denoDepsHash" system;
    buildPhase = ''
      export DENO_DIR=$TMPDIR/deno-cache
      export SSL_CERT_FILE=${cacert}/etc/ssl/certs/ca-bundle.crt
      export HOME=$TMPDIR

      # Run codegen (generates src/__codegen__/graphql.ts via npm:@graphql-codegen/cli)
      deno task codegen

      # Cache all modules so deno compile works offline
      deno cache src/main.ts
    '';
    installPhase = ''
      mkdir -p $out
      cp -r $TMPDIR/deno-cache $out/deno-cache
      cp -r src/__codegen__ $out/codegen
    '';
  };
in
stdenvNoCC.mkDerivation {
  pname = "linear-cli";
  inherit version;
  src = inputs.linear-cli;
  nativeBuildInputs = [
    deno
    installShellFiles
  ];
  buildPhase = ''
    export DENO_DIR=$(mktemp -d)
    cp -r ${denoDeps}/deno-cache/* $DENO_DIR/
    chmod -R u+w $DENO_DIR
    export HOME=$TMPDIR

    # Copy generated codegen files into source tree
    mkdir -p src/__codegen__
    cp -r ${denoDeps}/codegen/* src/__codegen__/

    deno compile -A --output linear src/main.ts
  '';
  installPhase = ''
    mkdir -p $out/bin
    cp linear $out/bin/

    installShellCompletion --cmd linear \
      --bash <($out/bin/linear completions bash) \
      --fish <($out/bin/linear completions fish) \
      --zsh <($out/bin/linear completions zsh)
  '';
  meta = with lib; {
    description = "Linear issue tracker CLI";
    homepage = "https://github.com/schpet/linear-cli";
    license = licenses.isc;
    mainProgram = "linear";
  };
}
